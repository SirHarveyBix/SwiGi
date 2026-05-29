"""Path PUSH — keyboard watcher pour Gen S (HID++ >= 4.5).

Capture la notification CHANGE_HOST envoyée par le firmware avant déconnexion BT.
Fallback PULL à la reconnexion si la notification est perdue (race macOS kernel).
"""

import logging
import queue
import threading
import time

from swigi.constants import (
    DEVICE_NUMBER_DIRECT,
    MSG_LENGTHS,
    PING_MESSAGE,
)
from swigi.discovery import DeviceInfo
from swigi.protocol import get_current_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.path_push")

# ── Constantes ────────────────────────────────────────────────────────────────

_PING_INTERVAL = 0.5
_READ_WINDOW = 0.5
_DEBOUNCE = 1.0
_WATCHDOG_TIMEOUT = 10.0


# ── Drain switch ──────────────────────────────────────────────────────────────


def _drain_switch(keyboard: DeviceInfo) -> int | None:
    """Lit jusqu'à 10 paquets pour capturer un switch buffered avant déconnexion."""
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


# ── Watcher principal ─────────────────────────────────────────────────────────


def watch_keyboard_push(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Watcher Gen S : capture notification CHANGE_HOST + fallback PULL."""
    from swigi.daemon import (
        _post_pull_event,
        _reconnect_keyboard,
        _set_keyboard_status,
        _SwitchEvent,
    )

    name = keyboard.name
    last_response = time.time()
    last_ping = 0.0
    last_switch_time = 0.0
    last_switch_target = -1

    # Déterminer l'index de ce Mac
    try:
        this_mac_host = get_current_host(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index
        )
        if this_mac_host is not None:
            state["this_mac_host"] = this_mac_host
            log.info(
                "⌨️  [%s] Surveillance PUSH démarrée (hôte %d)", name, this_mac_host + 1
            )
        else:
            log.info("⌨️  [%s] Surveillance PUSH démarrée", name)
    except (TransportError, OSError):
        log.info("⌨️  [%s] Surveillance PUSH démarrée", name)

    while not stop_event.is_set():
        # Watchdog
        if time.time() - last_response > _WATCHDOG_TIMEOUT:
            log.warning("👁️  [%s] Pas de réponse → reconnexion", name)
            keyboard.close()
            _set_keyboard_status(state, keyboard.product_id, name, False)
            keyboard = _reconnect_keyboard(keyboard.product_id, stop_event)
            if keyboard is None:
                break
            name = keyboard.name
            _set_keyboard_status(state, keyboard.product_id, name, True)
            log.info("🔄 ⌨️ [%s] Reconnecté", name)
            _post_pull_event(keyboard, event_queue, state, hunt_trigger, name)
            last_response = time.time()
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
                    event_queue.put(_SwitchEvent(target, name, "push"))

                log.info("🔌 ⌨️ [%s] Déconnecté", name)
                keyboard.close()
                _set_keyboard_status(state, keyboard.product_id, name, False)
                keyboard = _reconnect_keyboard(keyboard.product_id, stop_event)
                if keyboard is None:
                    break
                name = keyboard.name
                _set_keyboard_status(state, keyboard.product_id, name, True)
                log.info("🔄 ⌨️ [%s] Reconnecté", name)
                if time.time() - last_switch_time > 5.0:
                    from swigi.gui import notify

                    notify(f"{name} reconnecté", "Clavier")
                _post_pull_event(keyboard, event_queue, state, hunt_trigger, name)
                last_response = time.time()
                continue

        # Lecture notifications (fenêtre READ_WINDOW)
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

            # CHANGE_HOST notification
            if raw[2] == keyboard.change_host_index and len(raw) > 5:
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
                event_queue.put(_SwitchEvent(target, name, "push"))
                break

        if not got_data:
            time.sleep(0.01)

    if keyboard is not None:
        keyboard.close()
    log.info("🔴 [%s] PUSH arrêté", name)
