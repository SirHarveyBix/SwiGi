"""SwiGi daemon — pipe unidirectionnel Easy-Switch clavier → souris.

Clavier notifie → SwiGi envoie CHANGE_HOST → souris bascule → log confirme.
"""

import dataclasses
import logging
import queue
import threading
import time

from swigi.constants import (
    DEVICE_NUMBER_DIRECT,
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    MSG_LENGTHS,
    PING_MESSAGE,
    SYSTEM,
)
from swigi.discovery import DeviceInfo, find_all_devices
from swigi.gui import _prefs_lock, notify, prefs
from swigi.protocol import get_current_host, send_change_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.daemon")

# ── Constantes patchables ─────────────────────────────────────────────────────

_PING_INTERVAL = 0.5
_READ_WINDOW = 0.5
_RECONNECT_DELAY = 0.5
_RECONNECT_MAX_DELAY = 5.0
_STABILITY_WAIT = 0.5
_PROBE_INTERVAL = 3.0
_PROBE_FAST_INTERVAL = 1.0
_PROBE_FAST_DURATION = 15.0
_DEBOUNCE = 1.0
_VERIFY_TIMEOUT = 30.0


# ── Événements ────────────────────────────────────────────────────────────────


@dataclasses.dataclass(slots=True)
class _SwitchEvent:
    target_host: int
    keyboard_name: str


# ── Reconnexion clavier ───────────────────────────────────────────────────────


def _reconnect_keyboard(
    product_id: int, stop_event: threading.Event
) -> DeviceInfo | None:
    """Backoff exponentiel jusqu'à retrouver le clavier ou stop."""
    delay = _RECONNECT_DELAY
    while not stop_event.is_set():
        time.sleep(delay)
        if stop_event.is_set():
            return None
        for keyboard in find_all_devices(DEVICE_TYPE_KEYBOARD):
            if keyboard.product_id == product_id:
                if _STABILITY_WAIT > 0:
                    time.sleep(_STABILITY_WAIT)
                try:
                    keyboard.transport.write(PING_MESSAGE)
                    return keyboard
                except (TransportError, OSError):
                    keyboard.close()
            else:
                keyboard.close()
        delay = min(delay * 1.5, _RECONNECT_MAX_DELAY)
    return None


# ── Thread clavier ────────────────────────────────────────────────────────────


def _watch_keyboard(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Écoute CHANGE_HOST, poste dans la queue, reconnecte si nécessaire."""
    name = keyboard.name
    last_response = time.time()
    last_ping = 0.0
    last_switch_time = 0.0
    last_switch_target = -1

    # Déterminer l'index de ce Mac via le clavier
    try:
        this_mac_host = get_current_host(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index
        )
        if this_mac_host is not None:
            state["this_mac_host"] = this_mac_host
            log.info("⌨️  [%s] Surveillance démarrée (hôte %d)", name, this_mac_host + 1)
        else:
            log.info("⌨️  [%s] Surveillance démarrée", name)
    except (TransportError, OSError):
        log.info("⌨️  [%s] Surveillance démarrée", name)

    while not stop_event.is_set():
        # Watchdog
        if time.time() - last_response > 10.0:
            log.warning("👁️  [%s] Pas de réponse → reconnexion", name)
            keyboard.close()
            _set_keyboard_status(state, keyboard.product_id, name, False)
            keyboard = _reconnect_keyboard(keyboard.product_id, stop_event)
            if keyboard is None:
                break
            name = keyboard.name
            _set_keyboard_status(state, keyboard.product_id, name, True)
            log.info("🔄 ⌨️ [%s] Reconnecté", name)
            notify(f"{name} reconnecté", "Clavier")
            last_response = time.time()
            _pull_mouse_on_reconnect(state)
            hunt_trigger.set()
            continue

        # Ping
        if time.time() - last_ping >= _PING_INTERVAL:
            try:
                keyboard.transport.write(PING_MESSAGE)
                last_ping = time.time()
            except (TransportError, OSError):
                # Drain : capturer un switch juste avant la déco
                target = _drain_switch(keyboard)
                if target is not None and not (
                    target == last_switch_target
                    and time.time() - last_switch_time < _DEBOUNCE
                ):
                    last_switch_time = time.time()
                    last_switch_target = target
                    log.info("★ [%s] Easy-Switch → hôte %d (drain)", name, target + 1)
                    event_queue.put(_SwitchEvent(target, name))

                log.info("🔌 [%s] Déconnecté", name)
                keyboard.close()
                _set_keyboard_status(state, keyboard.product_id, name, False)
                keyboard = _reconnect_keyboard(keyboard.product_id, stop_event)
                if keyboard is None:
                    break
                name = keyboard.name
                _set_keyboard_status(state, keyboard.product_id, name, True)
                log.info("🔄 ⌨️ [%s] Reconnecté", name)
                if time.time() - last_switch_time > 5.0:
                    notify(f"{name} reconnecté", "Clavier")
                last_response = time.time()
                _pull_mouse_on_reconnect(state)
                hunt_trigger.set()
                continue

        # Lecture notifications
        deadline = time.time() + _READ_WINDOW
        got_data = False
        while time.time() < deadline and not stop_event.is_set():
            try:
                raw = keyboard.transport.read(timeout=50)
            except (TransportError, OSError):
                break
            if not raw or len(raw) < 4:
                continue
            if raw[0] not in MSG_LENGTHS or len(raw) < MSG_LENGTHS[raw[0]]:
                continue
            last_response = time.time()
            got_data = True

            # CHANGE_HOST notification (accepte tout sw_id — le MX Keys
            # peut envoyer avec sw_id != 0 selon le firmware)
            if (
                raw[2] == keyboard.change_host_index
                and len(raw) > 5
            ):
                num_hosts = raw[4] or 3
                target = raw[5]
                if not (0 <= target < num_hosts):
                    continue
                if (
                    target == last_switch_target
                    and time.time() - last_switch_time < _DEBOUNCE
                ):
                    continue
                last_switch_time = time.time()
                last_switch_target = target
                log.info("━" * 40)
                log.info("★ [%s] Easy-Switch → hôte %d", name, target + 1)
                event_queue.put(_SwitchEvent(target, name))
                break

        if not got_data:
            time.sleep(0.01)

    if keyboard is not None:
        keyboard.close()
    log.info("🔴 [%s] Arrêté", name)


def _drain_switch(keyboard: DeviceInfo) -> int | None:
    """Lit jusqu'à 10 paquets pour capturer un switch en buffer avant déconnexion."""
    for _ in range(10):
        try:
            raw = keyboard.transport.read(timeout=200)
        except (TransportError, OSError):
            break
        if not raw or len(raw) < 6:
            continue
        if raw[0] not in MSG_LENGTHS or len(raw) < MSG_LENGTHS[raw[0]]:
            continue
        if raw[2] == keyboard.change_host_index:
            num_hosts = raw[4] or 3
            target = raw[5]
            if 0 <= target < num_hosts:
                return target
    return None


# ── Thread probe souris ───────────────────────────────────────────────────────


def _mice_probe_loop(
    mice: list[DeviceInfo],
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
    mouse_lock: threading.Lock,
) -> None:
    """Découvre les souris, vérifie le switch, applique BetterMouse."""
    fast_until = 0.0

    while not stop_event.is_set():
        in_fast = time.time() < fast_until
        interval = _PROBE_FAST_INTERVAL if in_fast else _PROBE_INTERVAL
        if hunt_trigger.wait(timeout=interval):
            hunt_trigger.clear()
            fast_until = time.time() + _PROBE_FAST_DURATION

        if stop_event.is_set():
            break

        # Découverte
        found = find_all_devices(DEVICE_TYPE_MOUSE)
        found_pids = {device.product_id for device in found}

        with mouse_lock:
            existing_pids = {device.product_id for device in mice}
            new_mice = []
            reconnected_mice = []
            for mouse in found:
                if mouse.product_id not in existing_pids:
                    mice.append(mouse)
                    new_mice.append(mouse)
                elif not any(
                    device.transport.is_open
                    for device in mice
                    if device.product_id == mouse.product_id
                ):
                    mice[:] = [
                        device
                        for device in mice
                        if device.product_id != mouse.product_id
                    ]
                    mice.append(mouse)
                    reconnected_mice.append(mouse)
                else:
                    mouse.close()
            # Retirer mortes — détecter les déconnexions
            disconnected = [
                device
                for device in mice
                if not device.transport.is_open and device.product_id not in found_pids
            ]
            mice[:] = [
                device
                for device in mice
                if device.transport.is_open or device.product_id in found_pids
            ]

        for mouse in disconnected:
            if state.get("last_target_host") is not None:
                log.info("🔌 🖱️ [%s] Déconnectée (switch en cours)", mouse.name)
            else:
                log.info("🔌 🖱️ [%s] Déconnectée (switch manuel ?)", mouse.name)
            notify(f"{mouse.name} déconnectée", "Souris")

        for mouse in reconnected_mice:
            log.info("🔄 🖱️ [%s] Reconnectée", mouse.name)
            notify(f"{mouse.name} reconnectée", "Souris")
            # Loguer l'hôte actuel pour traçabilité
            try:
                current = get_current_host(
                    mouse.transport,
                    DEVICE_NUMBER_DIRECT,
                    mouse.change_host_index,
                )
                if current is not None:
                    log.info("🖱️  [%s] Hôte actuel : %d", mouse.name, current + 1)
            except (TransportError, OSError):
                pass

        for mouse in new_mice:
            log.info("🖱️  Souris : %s (PID=0x%04X)", mouse.name, mouse.product_id)
            notify(f"{mouse.name} connectée", "Souris")

        # Vérification post-switch
        state_lock = state.get("_lock")
        if state_lock:
            with state_lock:
                target = state.get("last_target_host")
                switch_time = state.get("last_switch_time", 0.0)
        else:
            target = state.get("last_target_host")
            switch_time = state.get("last_switch_time", 0.0)

        if target is not None:
            if time.time() - switch_time > _VERIFY_TIMEOUT:
                log.warning("⚠ Timeout vérification hôte %d — abandon", target + 1)
                state["last_target_host"] = None
            else:
                with mouse_lock:
                    open_mice = [device for device in mice if device.transport.is_open]
                for mouse in open_mice:
                    try:
                        current = get_current_host(
                            mouse.transport,
                            DEVICE_NUMBER_DIRECT,
                            mouse.change_host_index,
                        )
                    except (TransportError, OSError):
                        mouse.close()
                        continue
                    if current == target:
                        log.info("✓ %s sur hôte %d — confirmé", mouse.name, target + 1)
                        state["last_target_host"] = None
                        _apply_better_mouse(mouse.name)
                        break
                    elif current is not None:
                        # Souris sur mauvais hôte → envoi différé (la commande
                        # n'a pas été reçue ou le dispatcher n'avait pas de souris)
                        log.info(
                            "→ %s sur hôte %d, envoi vers hôte %d",
                            mouse.name,
                            current + 1,
                            target + 1,
                        )
                        try:
                            send_change_host(
                                mouse.transport,
                                DEVICE_NUMBER_DIRECT,
                                mouse.change_host_index,
                                target,
                            )
                        except (TransportError, OSError):
                            mouse.close()
                        state["last_target_host"] = None
                        break
        else:
            # Pas de switch en cours — BetterMouse sur nouvelles souris
            for mouse in new_mice:
                if mouse.transport.is_open:
                    _apply_better_mouse(mouse.name)

        # Mise à jour état
        with mouse_lock:
            active = [device.name for device in mice if device.transport.is_open]
        state["mouse"] = active[0] if active else None
        state["mice"] = active

    with mouse_lock:
        for mouse in mice:
            mouse.close()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pull_mouse_on_reconnect(state: dict) -> None:
    """Après reconnexion clavier, positionner le target pour ramener la souris sur ce Mac."""
    this_mac_host = state.get("this_mac_host")
    if this_mac_host is None:
        return
    lock = state.get("_lock")
    if lock:
        with lock:
            state["last_target_host"] = this_mac_host
            state["last_switch_time"] = time.time()
    else:
        state["last_target_host"] = this_mac_host
        state["last_switch_time"] = time.time()
    log.info("🔁 Clavier revenu → ramener souris sur hôte %d", this_mac_host + 1)


def _set_keyboard_status(state: dict, product_id: int, name: str, ok: bool) -> None:
    """Met à jour le statut d'un clavier dans state."""
    lock = state.get("_lock")
    if lock:
        with lock:
            state["keyboards"][product_id] = {"name": name, "ok": ok}
            _sync_keyboard_display(state)
    else:
        state["keyboards"][product_id] = {"name": name, "ok": ok}
        _sync_keyboard_display(state)


def _sync_keyboard_display(state: dict) -> None:
    """Met à jour state['keyboard'] pour la GUI."""
    for keyboard_data in state.get("keyboards", {}).values():
        if keyboard_data.get("ok"):
            state["keyboard"] = keyboard_data["name"]
            return
    state["keyboard"] = None


def _apply_better_mouse(mouse_name: str | None = None) -> None:
    """Applique le profil BetterMouse si configuré. Silencieux en cas d'erreur."""
    if SYSTEM != "Darwin":
        return
    with _prefs_lock:
        if not prefs.get("better_mouse_auto_apply") or not prefs.get(
            "better_mouse_profile"
        ):
            return
        profile = prefs["better_mouse_profile"]
    try:
        from swigi.bettermouse import apply_profile

        apply_profile(profile, mouse_name=mouse_name)
        log.info("🐭 Profil '%s' appliqué", profile)
    except Exception as error:
        log.debug("🐭 BetterMouse : %s", error)


# ── Point d'entrée ────────────────────────────────────────────────────────────


def run_daemon(
    keyboards: list[DeviceInfo],
    mice: list[DeviceInfo],
    state: dict,
    stop_event: threading.Event,
) -> None:
    """Daemon simplifié. Switch immédiat, vérification par log, pas de correction agressive."""
    # Init state
    lock = threading.Lock()
    state["_lock"] = lock
    state["keyboards"] = {
        keyboard_device.product_id: {"name": keyboard_device.name, "ok": True}
        for keyboard_device in keyboards
    }
    state["keyboard"] = keyboards[0].name if keyboards else None
    state["mouse"] = mice[0].name if mice else None
    state["mice"] = [mouse_device.name for mouse_device in mice]
    state.setdefault("switches", 0)
    state["last_target_host"] = None
    state["last_switch_time"] = 0.0

    event_queue: queue.Queue = queue.Queue()
    mouse_lock = threading.Lock()
    hunt_trigger = threading.Event()
    mice_list = list(mice)

    # Threads clavier
    for keyboard in keyboards:
        threading.Thread(
            target=_watch_keyboard,
            args=(keyboard, event_queue, state, stop_event, hunt_trigger),
            name=f"keyboard-{keyboard.product_id:04X}",
            daemon=True,
        ).start()

    # Thread souris
    threading.Thread(
        target=_mice_probe_loop,
        args=(mice_list, state, stop_event, hunt_trigger, mouse_lock),
        name="mice-probe",
        daemon=True,
    ).start()

    log.info("🟢 Prêt — %d clavier(s), %d souris", len(keyboards), len(mice))

    # Boucle principale : dispatch immédiat
    while not stop_event.is_set():
        try:
            event = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if not isinstance(event, _SwitchEvent):
            continue

        with _prefs_lock:
            mouse_follow = prefs.get("mouse_follow", True)
        if not mouse_follow:
            log.info("→ Suivi désactivé — ignoré")
            continue

        # Envoi immédiat
        with mouse_lock:
            targets = [device for device in mice_list if device.transport.is_open]

            sent = 0
            for mouse in targets:
                try:
                    send_change_host(
                        mouse.transport,
                        DEVICE_NUMBER_DIRECT,
                        mouse.change_host_index,
                        event.target_host,
                    )
                    log.info("⚡ %s → hôte %d", mouse.name, event.target_host + 1)
                    sent += 1
                except (TransportError, OSError):
                    mouse.close()

        with lock:
            state["last_target_host"] = event.target_host
            state["last_switch_time"] = time.time()
            state["switches"] = state.get("switches", 0) + 1
        hunt_trigger.set()

        if sent == 0:
            log.warning("⚠ Aucune souris — retry au prochain probe")

    log.info("🔴 Arrêt. %d basculements.", state.get("switches", 0))
