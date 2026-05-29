"""Path PULL — keyboard watcher pour Legacy (HID++ < 4.5).

Pas de lecture HID++ stream (notification inutilisable sur Legacy).
Détecte la déconnexion via ping watchdog et reconnecte.
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


def _do_reconnect(keyboard, state, stop_event, event_queue, hunt_trigger):
    """Reconnecte le clavier, détermine l'hôte courant et poste un _SwitchEvent."""
    from swigi.daemon import (
        _reconnect_keyboard,
        _set_keyboard_status,
        _SwitchEvent,
    )

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

    # Déterminer l'hôte courant après reconnexion
    try:
        this_mac_host = get_current_host(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index
        )
    except (TransportError, OSError):
        this_mac_host = None

    if this_mac_host is not None:
        log.info("★ [%s] PULL — reconnexion → hôte %d", name, this_mac_host + 1)
        event_queue.put(_SwitchEvent(this_mac_host, name, "pull"))
        hunt_trigger.set()

    return keyboard


def watch_keyboard_pull(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Watcher Legacy : ping watchdog. Pas de lecture HID++."""
    name = keyboard.name
    last_response = time.time()

    # Déterminer l'index de ce Mac
    try:
        this_mac_host = get_current_host(
            keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index
        )
        if this_mac_host is not None:
            log.info(
                "⌨️ [%s] Surveillance PULL démarrée (hôte %d)", name, this_mac_host + 1
            )
        else:
            log.info("⌨️ [%s] Surveillance PULL démarrée", name)
    except (TransportError, OSError):
        log.info("⌨️ [%s] Surveillance PULL démarrée", name)

    while not stop_event.is_set():
        # 1. Ping write
        try:
            keyboard.transport.write(PING_MESSAGE)
        except (TransportError, OSError):
            # Déconnexion détectée — reconnexion immédiate
            log.info("🔌 ⌨️ [%s] Déconnecté", name)
            keyboard = _do_reconnect(
                keyboard, state, stop_event, event_queue, hunt_trigger
            )
            if keyboard is None:
                break
            name = keyboard.name
            from swigi.gui import notify

            notify(f"{name} reconnecté", "Clavier")
            last_response = time.time()
            continue

        # 2. Tentative lecture réponse ping → met à jour last_response si reçue
        try:
            raw = keyboard.transport.read(timeout=100)
            if raw and len(raw) >= 4:
                last_response = time.time()
        except (TransportError, OSError):
            pass

        # 3. Watchdog (atteignable car last_response uniquement mis à jour sur lecture)
        if time.time() - last_response > _WATCHDOG_TIMEOUT:
            log.warning("👁️  [%s] Watchdog → reconnexion", name)
            keyboard = _do_reconnect(
                keyboard, state, stop_event, event_queue, hunt_trigger
            )
            if keyboard is None:
                break
            name = keyboard.name
            last_response = time.time()
            continue

        # 4. Attente jusqu'au prochain ping (arrêt propre via stop_event)
        if stop_event.wait(timeout=_PING_INTERVAL):
            break

    if keyboard is not None:
        keyboard.close()
    log.info("🔴 [%s] PULL arrêté", name)
