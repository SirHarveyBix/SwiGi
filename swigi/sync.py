"""Coordination inter-Mac : broadcast UDP pour synchroniser le switch souris."""

import json
import logging
import socket
import threading
import time
import uuid
from collections.abc import Callable

log = logging.getLogger("swigi.sync")

_PORT = 37000
_MACHINE_ID = str(uuid.getnode())


def broadcast_switch(target_host: int) -> None:
    """Informe les autres Macs de switcher leur souris vers target_host."""
    msg = json.dumps({"target": target_host, "from": _MACHINE_ID}).encode()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(msg, ("255.255.255.255", _PORT))
    except OSError as e:
        log.debug("broadcast_switch: %s", e)


def start_sync_listener(
    callback: Callable[[int], None], stop_event: threading.Event
) -> threading.Thread:
    """Écoute les broadcasts LAN des autres Macs. Appelle callback(target_host) à réception."""

    def _listen() -> None:
        # Pas de SO_REUSEPORT : un seul listener par Mac.
        # SO_REUSEPORT distribuerait les paquets entre plusieurs sockets (hash déterministe)
        # → si une ancienne instance tient le port, tous les paquets lui iraient, jamais à nous.
        for attempt in range(3):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.bind(("", _PORT))
                    s.settimeout(1.0)
                    log.info("🔗 Sync inter-Mac actif (UDP :%d)", _PORT)
                    while not stop_event.is_set():
                        try:
                            data, _ = s.recvfrom(256)
                            msg = json.loads(data)
                            if not isinstance(msg, dict):
                                continue
                            if msg.get("from") == _MACHINE_ID:
                                continue  # ignore son propre broadcast
                            target = msg.get("target")
                            if isinstance(target, int) and 0 <= target <= 9:
                                callback(target)
                        except TimeoutError:
                            pass
                        except Exception as e:
                            log.debug("sync listener: %s", e)
                    return  # stop_event set, sortie propre
            except OSError:
                if attempt < 2 and not stop_event.is_set():
                    time.sleep(0.5)  # launchd restart : port brièvement occupé
                    continue
                log.warning(
                    "sync listener bind échoué (port %d) — sync inter-Mac désactivé "
                    "(vérifier qu'une seule instance SwiGi tourne)",
                    _PORT,
                )

    t = threading.Thread(target=_listen, name="sync-listener", daemon=True)
    t.start()
    return t
