"""SwiGi daemon — orchestrateur PUSH.

Dispatcher unifié : easy-switch event → send_change_host aux souris.
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
    PING_MESSAGE,
    SYSTEM,
)
from swigi.discovery import DeviceInfo, find_all_devices
from swigi.gui import _prefs_lock, notify, prefs
from swigi.protocol import send_change_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.daemon")

# ── Constantes patchables ─────────────────────────────────────────────────────

_PROBE_INTERVAL = 3.0
_PROBE_FAST_INTERVAL = 1.0
_PROBE_FAST_DURATION = 15.0
_DISPATCHER_DEBOUNCE = 1.0
_RECONNECT_DELAY = 0.5
_RECONNECT_MAX_DELAY = 5.0
_STABILITY_WAIT = 0.5
_VERIFY_TIMEOUT = 5.0        # s — TTL last_target_host : au-delà, l'utilisateur a peut-être changé la souris manuellement


# ── Événements ────────────────────────────────────────────────────────────────


@dataclasses.dataclass(slots=True)
class _SwitchEvent:
    target_host: int
    keyboard_name: str
    source: str  # "push"


# ── Helpers partagés ─────────────────────────────────────────────────────────


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
                time.sleep(_STABILITY_WAIT)
                try:
                    # Vider le buffer stale (max 10 lectures)
                    for _ in range(10):
                        if not keyboard.transport.read(timeout=10):
                            break
                    # Ping + vérification réponse
                    keyboard.transport.write(PING_MESSAGE)
                    response = keyboard.transport.read(timeout=200)
                    if response:
                        return keyboard
                    # Pas de réponse → clavier pas vraiment ici
                    keyboard.close()
                except (TransportError, OSError):
                    keyboard.close()
            else:
                keyboard.close()
        delay = min(delay * 1.5, _RECONNECT_MAX_DELAY)
    return None


def _set_keyboard_status(state: dict, product_id: int, name: str, ok: bool) -> None:
    """Met à jour le statut d'un clavier dans state."""
    with state["_lock"]:
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


# ── Thread probe souris ───────────────────────────────────────────────────────


def _mice_probe_loop(
    mice: list[DeviceInfo],
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
    mouse_lock: threading.Lock,
) -> None:
    """Découvre les souris et applique BetterMouse."""
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
        try:
            found = find_all_devices(DEVICE_TYPE_MOUSE)
        except Exception:
            log.exception("Erreur enumerate souris — probe ignorée")
            continue
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
            # Retirer mortes
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
            log.info("🔌 🖱️ [%s] Déconnectée", mouse.name)
            notify(f"{mouse.name} déconnectée", "Souris")

        for mouse in reconnected_mice:
            log.info("🔄 🖱️ [%s] Reconnectée", mouse.name)
            notify(f"{mouse.name} reconnectée", "Souris")

        for mouse in new_mice:
            log.info("🖱️ [%s] Connectée (PID=0x%04X)", mouse.name, mouse.product_id)
            notify(f"{mouse.name} connectée", "Souris")

        for mouse in new_mice + reconnected_mice:
            if mouse.transport.is_open:
                _apply_better_mouse(mouse.name)

        # Envoi différé post-switch (souris indisponible au moment du dispatch)
        with state["_lock"]:
            target = state.get("last_target_host")
            if target is not None and time.time() - state.get("last_switch_time", 0.0) > _VERIFY_TIMEOUT:
                log.warning("⏳ TTL %ds expiré — switch hôte %d perdu (aucune souris n'a répondu)", int(_VERIFY_TIMEOUT), target + 1)
                state["last_target_host"] = None
                target = None

        if target is not None:
            with mouse_lock:
                open_mice = [m for m in mice if m.transport.is_open]
            sent = 0
            for mouse in open_mice:
                try:
                    send_change_host(
                        mouse.transport,
                        DEVICE_NUMBER_DIRECT,
                        mouse.change_host_index,
                        target,
                    )
                    log.info("⚡ %s → hôte %d (différé)", mouse.name, target + 1)
                    _apply_better_mouse(mouse.name)
                    sent += 1
                except (TransportError, OSError):
                    with mouse_lock:
                        mouse.close()
            if sent > 0:
                with state["_lock"]:
                    state["last_target_host"] = None

        # Mise à jour état
        with mouse_lock:
            active = [device.name for device in mice if device.transport.is_open]
        with state["_lock"]:
            state["mouse"] = active[0] if active else None
            state["mice"] = active

    with mouse_lock:
        for mouse in mice:
            mouse.close()


# ── Point d'entrée ────────────────────────────────────────────────────────────


def run_daemon(
    keyboards: list[DeviceInfo],
    mice: list[DeviceInfo],
    state: dict,
    stop_event: threading.Event,
) -> None:
    """Daemon PUSH. Lance un watcher par clavier, dispatch les events aux souris."""
    from swigi.path_push import watch_keyboard_push

    # Init state — préserver le lock et le compteur à travers les restarts
    if "_lock" not in state:
        state["_lock"] = threading.Lock()
    state["keyboards"] = {
        keyboard_device.product_id: {"name": keyboard_device.name, "ok": True}
        for keyboard_device in keyboards
    }
    state["keyboard"] = keyboards[0].name if keyboards else None
    state["mouse"] = mice[0].name if mice else None
    state["mice"] = [mouse_device.name for mouse_device in mice]
    state["switches"] = state.get("switches", 0)
    state["last_target_host"] = None
    state["last_switch_time"] = 0.0

    event_queue: queue.Queue = queue.Queue()
    mouse_lock = threading.Lock()
    hunt_trigger = threading.Event()
    mice_list = list(mice)

    # Threads clavier
    for keyboard in keyboards:
        if not keyboard.push_capable:
            log.warning(
                "⚠️ ⌨️ [%s] Non pris en charge — ancienne génération (Gen S requis)",
                keyboard.name,
            )
            keyboard.close()
            continue
        log.info("⌨️ [%s] → path PUSH", keyboard.name)
        threading.Thread(
            target=watch_keyboard_push,
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

    # Boucle principale : dispatch unifié
    last_dispatch_target = -1
    last_dispatch_time = 0.0

    while not stop_event.is_set() or not event_queue.empty():
        try:
            event = event_queue.get(timeout=0.1)
        except queue.Empty:
            if stop_event.is_set():
                break
            continue

        # Debounce : même target < 1s → drop
        if (
            event.target_host == last_dispatch_target
            and time.time() - last_dispatch_time < _DISPATCHER_DEBOUNCE
        ):
            log.debug(
                "⏭️  Debounce : hôte %d déjà dispatché < 1s", event.target_host + 1
            )
            continue

        with _prefs_lock:
            mouse_follow = prefs.get("mouse_follow", True)
        if not mouse_follow:
            log.info("🚫 Suivi souris désactivé — switch ignoré")
            continue

        with mouse_lock:
            targets = [device for device in mice_list if device.transport.is_open]

        # HID I/O hors lock — évite blocage de _mice_probe_loop
        failed_mice = []
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
                _apply_better_mouse(mouse.name)
                sent += 1
            except (TransportError, OSError) as e:
                log.warning("⚠️ 🖱️ [%s] Envoi CHANGE_HOST échoué (%s)", mouse.name, e)
                failed_mice.append(mouse)

        if failed_mice:
            with mouse_lock:
                for mouse in failed_mice:
                    mouse.close()

        with state["_lock"]:
            state["switches"] += 1
            if sent > 0:
                state["last_target_host"] = None
            else:
                state["last_target_host"] = event.target_host
                state["last_switch_time"] = time.time()
                if not targets:
                    log.warning("⚠️ Aucune souris disponible — switch hôte %d différé", event.target_host + 1)
                else:
                    log.warning("⚠️ Toutes les souris ont échoué — switch hôte %d différé", event.target_host + 1)
        hunt_trigger.set()
        last_dispatch_target = event.target_host
        last_dispatch_time = time.time()

    log.info("🔴 Arrêt. %d basculements.", state["switches"])
