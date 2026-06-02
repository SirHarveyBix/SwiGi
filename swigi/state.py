"""Helpers d'état partagés entre daemon et path_push.

Ce module casse le cycle d'import circulaire daemon ↔ path_push :
- daemon.py et path_push.py importent tous les deux depuis state.py
- state.py ne dépend ni de daemon ni de path_push
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time

from swigi.constants import (
    DEVICE_TYPE_KEYBOARD,
    PING_MESSAGE,
)
from swigi.discovery import DeviceInfo, find_all_devices
from swigi.transport import TransportError

log = logging.getLogger("swigi.state")

# Constantes de reconnexion (patchables dans les tests)
_RECONNECT_DELAY = 0.5
_RECONNECT_MAX_DELAY = 5.0
_STABILITY_WAIT = 0.5


@dataclasses.dataclass(slots=True)
class _SwitchEvent:
    target_host: int
    keyboard_name: str
    source: str  # "push"


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
                    for _ in range(10):
                        if not keyboard.transport.read(timeout=10):
                            break
                    keyboard.transport.write(PING_MESSAGE)
                    response = keyboard.transport.read(timeout=200)
                    if response:
                        return keyboard
                    keyboard.close()
                except (TransportError, OSError):
                    keyboard.close()
            else:
                keyboard.close()
        delay = min(delay * 1.5, _RECONNECT_MAX_DELAY)
    return None


def _sync_keyboard_display(state: dict) -> None:
    """Met à jour state['keyboard'] pour la GUI."""
    for keyboard_data in state.get("keyboards", {}).values():
        if keyboard_data.get("ok"):
            state["keyboard"] = keyboard_data["name"]
            return
    state["keyboard"] = None


def _set_keyboard_status(state: dict, product_id: int, name: str, ok: bool) -> None:
    """Met à jour le statut d'un clavier dans state."""
    with state["_lock"]:
        state["keyboards"][product_id] = {"name": name, "ok": ok}
        _sync_keyboard_display(state)
