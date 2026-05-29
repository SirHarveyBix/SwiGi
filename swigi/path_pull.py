"""Path PULL — keyboard watcher pour Legacy (HID++ < 4.5).

Pas de lecture HID++ stream (notification inutilisable sur Legacy).
Détecte la déconnexion via ping watchdog, reconnecte, puis PULL souris.
"""

import logging
import queue
import threading
import time

from swigi.constants import (
    DEVICE_NUMBER_DIRECT,
    PING_MESSAGE,
)
from swigi.discovery import DeviceInfo
from swigi.protocol import get_current_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.path_pull")

# ── Constantes ────────────────────────────────────────────────────────────────

_PING_INTERVAL = 0.5
_WATCHDOG_TIMEOUT = 10.0


# ── Watcher principal ─────────────────────────────────────────────────────────


def watch_keyboard_pull(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Watcher Legacy : ping watchdog + PULL on reconnect. Pas de lecture HID++."""
    from swigi.daemon import (
        _post_pull_event,
        _reconnect_keyboard,
        _set_keyboard_status,
    )

    name = keyboard.name
    last_response = time.time()

    # Déterminer l'index de ce Mac
    try:
        this_mac_host = get_current_host(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index
        )
        if this_mac_host is not None:
            state["this_mac_host"] = this_mac_host
            log.info(
                "⌨️  [%s] Surveillance PULL démarrée (hôte %d)", name, this_mac_host + 1
            )
        else:
            log.info("⌨️  [%s] Surveillance PULL démarrée", name)
    except (TransportError, OSError):
        log.info("⌨️  [%s] Surveillance PULL démarrée", name)

    while not stop_event.is_set():
        # Ping
        try:
            keyboard.transport.write(PING_MESSAGE)
            last_response = time.time()
        except (TransportError, OSError):
            # Déconnexion détectée — reconnexion immédiate
            log.info("🔌 ⌨️ [%s] Déconnecté", name)
            keyboard.close()
            _set_keyboard_status(state, keyboard.product_id, name, False)
            keyboard = _reconnect_keyboard(keyboard.product_id, stop_event)
            if keyboard is None:
                break
            name = keyboard.name
            _set_keyboard_status(state, keyboard.product_id, name, True)
            log.info("🔄 ⌨️ [%s] Reconnecté", name)
            from swigi.gui import notify

            notify(f"{name} reconnecté", "Clavier")
            _post_pull_event(keyboard, event_queue, state, hunt_trigger, name)
            last_response = time.time()
            continue

        # Watchdog (si ping réussit mais device stale)
        if time.time() - last_response > _WATCHDOG_TIMEOUT:
            log.warning("👁️  [%s] Watchdog → reconnexion", name)
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

        # Sleep entre les pings — pas de lecture HID++
        time.sleep(_PING_INTERVAL)

    if keyboard is not None:
        keyboard.close()
    log.info("🔴 [%s] PULL arrêté", name)
