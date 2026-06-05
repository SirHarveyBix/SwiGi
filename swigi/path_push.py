"""Path PUSH — keyboard watcher pour Gen S (HID++ >= 4.5).

Capture la notification CHANGE_HOST envoyée par le firmware avant déconnexion BT.
"""

import logging
import queue
import threading
import time

from swigi.constants import (
    DEVICE_NUMBER_DIRECT,
    MSG_LENGTHS,
    PING_MESSAGE,
    SW_ID,
)
from swigi.discovery import DeviceInfo
from swigi.gui import notify
from swigi.prefs import _prefs_lock, prefs, save_prefs
from swigi.protocol import get_backlight_config, get_host_info, set_backlight_config
from swigi.state import _reconnect_keyboard, _set_keyboard_status, _SwitchEvent
from swigi.transport import TransportError

log = logging.getLogger("swigi.path_push")

# ── Constantes ────────────────────────────────────────────────────────────────

_PING_INTERVAL = 0.5
_READ_WINDOW = 0.5
_DEBOUNCE = 1.0
_WATCHDOG_TIMEOUT = 10.0
_STALE_PING_TIMEOUT = 100   # ms — BLE Logitech RTT ≈ 15-30 ms ; 100 ms = marge x3
_RECONNECT_STALE_WINDOW = 2.0  # s — fenêtre post-reconnect : Mac receveur (firmware redelivre notif stale)
# MX Keys S prend > 200ms pour finir la déco BLE après notification CHANGE_HOST.
# Polling toutes les 50ms jusqu'à déco confirmée (max 2s = stale confirmé).
_STALE_POLL_INTERVAL = 0.05  # s — intervalle entre pings de confirmation
_STALE_MAX_PINGS = 40        # 40 × 50ms = 2s max avant de confirmer stale
_ARRIVAL_SWITCH_DELAY = 3.0  # s — seuil min d'absence pour distinguer switch réel d'un blip BT


# ── Backlight helpers ─────────────────────────────────────────────────────────


def _save_initial_backlight(keyboard: DeviceInfo) -> None:
    """À la première connexion, lit le level actuel et le sauvegarde dans les prefs."""
    if keyboard.backlight_index is None:
        return
    key = f"backlight_{keyboard.product_id}"
    with _prefs_lock:
        if key in prefs:
            return
    try:
        config = get_backlight_config(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.backlight_index, timeout=300
        )
        if config:
            with _prefs_lock:
                prefs[key] = config[0]
                save_prefs(dict(prefs))
            log.info("💡 [%s] Level rétroéclairage initial sauvegardé : %d%%", keyboard.name, config[0])
    except (TransportError, OSError) as e:
        log.debug("💡 [%s] Lecture backlight initiale échouée : %s", keyboard.name, e)


def _restore_backlight(keyboard: DeviceInfo) -> None:
    """Restaure le rétroéclairage depuis les prefs (après reconnexion BT)."""
    if keyboard.backlight_index is None:
        return
    key = f"backlight_{keyboard.product_id}"
    with _prefs_lock:
        level = prefs.get(key)
    if level is None:
        return
    try:
        ok = set_backlight_config(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.backlight_index, level, timeout=500
        )
        if ok:
            log.info("💡 [%s] Rétroéclairage restauré → %d%%", keyboard.name, level)
        else:
            log.debug("💡 [%s] Restauration backlight : pas de réponse", keyboard.name)
    except (TransportError, OSError) as e:
        log.debug("💡 [%s] Restauration backlight échouée : %s", keyboard.name, e)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _drain_switch(keyboard: DeviceInfo) -> int | None:
    """Lit jusqu'à 10 paquets pour capturer un switch buffered avant déconnexion."""
    for _ in range(10):
        try:
            raw = keyboard.transport.read(timeout=200)
        except (TransportError, OSError):
            break
        if raw is None:
            break
        if len(raw) < 6:
            continue  # paquet court — continuer, ne pas stopper le drain
        if raw[0] not in MSG_LENGTHS or len(raw) < MSG_LENGTHS[raw[0]]:
            continue
        if raw[2] == keyboard.change_host_index and (raw[3] & 0x0F) == 0x00:
            num_hosts = raw[4] if raw[4] > 0 else 3
            target = raw[5]
            if 0 <= target < num_hosts:
                return target
    return None


def _is_stale_notification(keyboard: DeviceInfo) -> bool:
    """Retourne True si le clavier est encore connecté (notification stale).

    Un clavier en cours de déconnexion (vrai Easy-Switch) ne répondra pas
    dans le délai imparti. Un clavier connecté et stable répondra.
    """
    try:
        keyboard.transport.write(PING_MESSAGE)
        resp = keyboard.transport.read(timeout=_STALE_PING_TIMEOUT)
        # Vérifie que la réponse correspond bien au PING (feature 0x00, SW_ID)
        return resp is not None and len(resp) > 3 and resp[2] == 0x00 and resp[3] == SW_ID
    except (TransportError, OSError):
        return False  # pas de réponse = déconnexion = switch réel


def _emit_drain_switch(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    hunt_trigger: threading.Event,
    this_mac_host: int | None,
    last_switch_time: float,
    last_switch_target: int,
    name: str,
    log_suffix: str,
) -> tuple[float, int]:
    """Vide le buffer HID, émet un _SwitchEvent si un switch y est buffered.

    Retourne (last_switch_time, last_switch_target) mis à jour.
    """
    target = _drain_switch(keyboard)
    if target is not None and not (
        target == last_switch_target and time.time() - last_switch_time < _DEBOUNCE
    ) and not (this_mac_host is not None and target == this_mac_host):
        last_switch_time = time.time()
        last_switch_target = target
        log.info("★ [%s] Easy-Switch → hôte %d (%s)", name, target + 1, log_suffix)
        event_queue.put(_SwitchEvent(target, name, "push"))
        hunt_trigger.set()
    return last_switch_time, last_switch_target


def _emit_arrival_switch(
    reconnect_duration: float,
    this_mac_host: int | None,
    event_queue: queue.Queue,
    hunt_trigger: threading.Event,
    last_switch_time: float,
    last_switch_target: int,
    name: str,
) -> tuple[float, int]:
    """Émet switch vers ce Mac si le clavier était absent assez longtemps (switch réel, pas blip).

    Appelé après reconnexion. Fonctionne pour :
    - Mac source (MX Keys S) : clavier revient après switch→switch retour
    - Mac destination : clavier arrive depuis un autre Mac (host index = ce Mac)
    """
    if reconnect_duration <= _ARRIVAL_SWITCH_DELAY or this_mac_host is None:
        return last_switch_time, last_switch_target
    target = this_mac_host
    if target == last_switch_target and time.time() - last_switch_time < _DEBOUNCE:
        return last_switch_time, last_switch_target
    log.info("★ [%s] Easy-Switch → hôte %d (arrival)", name, target + 1)
    event_queue.put(_SwitchEvent(target, name, "push"))
    hunt_trigger.set()
    return time.time(), target


def _do_reconnect_push(
    keyboard: DeviceInfo,
    state: dict,
    stop_event: threading.Event,
) -> "DeviceInfo | None":
    """Reconnecte clavier PUSH. Easy-Switch seul déclenche les switchs souris."""
    name = keyboard.name
    product_id = keyboard.product_id
    keyboard.close()
    _set_keyboard_status(state, product_id, name, False)
    keyboard = _reconnect_keyboard(product_id, stop_event)
    if keyboard is None:
        return None
    name = keyboard.name
    _set_keyboard_status(state, product_id, name, True)
    log.info("🔄 ⌨️ [%s] Reconnecté", name)
    return keyboard


# ── Watcher principal ─────────────────────────────────────────────────────────


def watch_keyboard_push(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Watcher Gen S : capture notification CHANGE_HOST."""
    name = keyboard.name
    last_response = time.time()
    last_ping = 0.0
    last_switch_time = 0.0
    last_switch_target = -1
    this_mac_host = None
    num_hosts = 3  # default — mis à jour via get_host_info
    # Initialisé à now : sur Mac receveur (fresh connect), le firmware redelivre la dernière
    # notification 0.5-3s après connexion BT. La fenêtre anti-stale est active dès le départ.
    reconnect_time = time.time()

    try:
        info = get_host_info(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index,
            timeout=200,
        )
        if info is not None:
            num_hosts, this_mac_host = info
        suffix = f" (hôte {this_mac_host + 1})" if this_mac_host is not None else ""
        log.info("⌨️ [%s] Surveillance PUSH démarrée%s", name, suffix)
    except (TransportError, OSError):
        log.info("⌨️ [%s] Surveillance PUSH démarrée", name)

    _save_initial_backlight(keyboard)

    while not stop_event.is_set():
        # Backlight dirty (changement via GUI) — appliqué ici pour ne pas bloquer la GUI
        if state.pop("backlight_dirty", False) and keyboard.backlight_index is not None:
            _restore_backlight(keyboard)
        # Watchdog
        if time.time() - last_response > _WATCHDOG_TIMEOUT:
            log.warning("👁️  [%s] Pas de réponse → reconnexion", name)
            keyboard = _do_reconnect_push(keyboard, state, stop_event)
            if keyboard is None:
                break
            name = keyboard.name
            try:
                info = get_host_info(
                    keyboard.transport, DEVICE_NUMBER_DIRECT,
                    keyboard.change_host_index, timeout=200,
                )
                if info is not None:
                    num_hosts, this_mac_host = info
            except (TransportError, OSError):
                pass
            _restore_backlight(keyboard)
            last_response = time.time()
            reconnect_time = time.time()
            continue

        # Ping keepalive
        if time.time() - last_ping >= _PING_INTERVAL:
            try:
                keyboard.transport.write(PING_MESSAGE)
                last_ping = time.time()
            except (TransportError, OSError):
                last_switch_time, last_switch_target = _emit_drain_switch(
                    keyboard, event_queue, hunt_trigger, this_mac_host,
                    last_switch_time, last_switch_target, name, "drain",
                )
                log.info("🔌 ⌨️ [%s] Déconnecté", name)
                _disconnect_time = time.time()
                keyboard = _do_reconnect_push(keyboard, state, stop_event)
                if keyboard is None:
                    break
                name = keyboard.name
                _reconnect_duration = time.time() - _disconnect_time
                try:
                    info = get_host_info(
                        keyboard.transport, DEVICE_NUMBER_DIRECT,
                        keyboard.change_host_index, timeout=200,
                    )
                    if info is not None:
                        num_hosts, this_mac_host = info
                except (TransportError, OSError):
                    pass
                last_switch_time, last_switch_target = _emit_arrival_switch(
                    _reconnect_duration, this_mac_host,
                    event_queue, hunt_trigger, last_switch_time, last_switch_target, name,
                )
                _restore_backlight(keyboard)
                if time.time() - last_switch_time > 5.0:
                    notify(f"{name} reconnecté", "Clavier")
                last_response = time.time()
                reconnect_time = time.time()
                continue

        # Lecture bloquante — une seule lecture par cycle, faible latence de détection
        try:
            raw = keyboard.transport.read(timeout=int(_READ_WINDOW * 1000))
        except (TransportError, OSError):
            last_switch_time, last_switch_target = _emit_drain_switch(
                keyboard, event_queue, hunt_trigger, this_mac_host,
                last_switch_time, last_switch_target, name, "drain-read",
            )
            log.info("🔌 ⌨️ [%s] Déconnecté (lecture)", name)
            _disconnect_time = time.time()
            keyboard = _do_reconnect_push(keyboard, state, stop_event)
            if keyboard is None:
                break
            name = keyboard.name
            _reconnect_duration = time.time() - _disconnect_time
            try:
                info = get_host_info(
                    keyboard.transport, DEVICE_NUMBER_DIRECT,
                    keyboard.change_host_index, timeout=200,
                )
                if info is not None:
                    num_hosts, this_mac_host = info
            except (TransportError, OSError):
                pass
            last_switch_time, last_switch_target = _emit_arrival_switch(
                _reconnect_duration, this_mac_host,
                event_queue, hunt_trigger, last_switch_time, last_switch_target, name,
            )
            _restore_backlight(keyboard)
            if time.time() - last_switch_time > 5.0:
                notify(f"{name} reconnecté", "Clavier")
            last_response = time.time()
            reconnect_time = time.time()
            continue

        if not raw or len(raw) < 4:
            continue

        if raw[0] not in MSG_LENGTHS or len(raw) < MSG_LENGTHS[raw[0]]:
            continue

        last_response = time.time()

        # Notification CHANGE_HOST (sw_id=0 = firmware, pas réponse à notre requête)
        if (
            raw[2] == keyboard.change_host_index
            and len(raw) > 5
            and (raw[3] & 0x0F) == 0x00
        ):
            num_hosts = raw[4] if raw[4] > 0 else 3
            target = raw[5]
            if not (0 <= target < num_hosts):
                continue
            # Ignorer : clavier reste sur ce Mac (reconnect artifact firmware)
            if this_mac_host is not None and target == this_mac_host:
                log.debug("⏭️  [%s] Notification ignorée — hôte local", name)
                continue
            # Anti-stale : ping uniquement dans la fenêtre post-reconnect (Mac receveur).
            # Hors fenêtre (Mac source, clavier connecté depuis longtemps) → notification réelle.
            # Phase 1 : si le clavier répond au ping, AMBIGU — Gen S envoie la notif avant de
            # déconnecter le BT. Polling jusqu'à déco confirmée ou timeout (stale).
            # MX Keys S peut prendre > 200ms pour terminer la déco BLE.
            if time.time() - reconnect_time < _RECONNECT_STALE_WINDOW and _is_stale_notification(keyboard):
                _poll_start = time.time()
                _is_real = False
                for _ in range(_STALE_MAX_PINGS):
                    if stop_event.is_set():
                        break
                    time.sleep(_STALE_POLL_INTERVAL)
                    if not _is_stale_notification(keyboard):
                        _is_real = True
                        break
                if not _is_real:
                    log.info("⏭️  [%s] Notification stale confirmée (%.0fms) ; fenêtre fermée",
                             name, (time.time() - _poll_start) * 1000)
                    reconnect_time = 0.0
                    continue
                log.info("⚡ [%s] Switch réel confirmé — déco BLE en %.0fms",
                         name, (time.time() - _poll_start) * 1000)
            # Debounce : même cible < 1s
            if target == last_switch_target and time.time() - last_switch_time < _DEBOUNCE:
                continue
            last_switch_time = time.time()
            last_switch_target = target
            log.info("★ [%s] Easy-Switch → hôte %d", name, target + 1)
            event_queue.put(_SwitchEvent(target, name, "push"))
            hunt_trigger.set()

    if keyboard is not None:
        keyboard.close()
    log.info("🔴 [%s] PUSH arrêté", name)
