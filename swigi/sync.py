"""SwiGi sync — diffusion UDP inter-Mac pour coordonner le switch de la souris.

Quand le clavier arrive sur Mac N (ex : MX Keys S sans notification PUSH),
Mac N broadcast "switch souris → hôte N". Le Mac qui a la souris reçoit,
switch immédiatement.
"""

import json
import logging
import socket
import threading
import uuid

log = logging.getLogger("swigi.sync")

SYNC_PORT = 37000
_BCAST_ADDR = "255.255.255.255"
_MACHINE_ID = str(uuid.getnode())


def broadcast_switch(target_host: int, port: int = SYNC_PORT) -> None:
    """Diffuse 'switch souris → hôte N' sur le réseau local (UDP broadcast)."""
    msg = json.dumps({"a": "sw", "h": target_host, "id": _MACHINE_ID}).encode()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(0.5)
            s.sendto(msg, (_BCAST_ADDR, port))
        log.debug("📡 Broadcast switch → hôte %d", target_host + 1)
    except OSError as exc:
        log.debug("📡 Broadcast échoué : %s", exc)


def start_sync_listener(
    callback,
    stop_event: threading.Event,
    port: int = SYNC_PORT,
) -> threading.Thread:
    """Lance thread UDP. Appelle callback(target_host: int) sur chaque broadcast reçu.

    Filtre les broadcasts émis par ce Mac (self-echo).
    """

    def _listen() -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("", port))
                s.settimeout(1.0)
                log.info("📡 Sync UDP actif (port %d)", port)
                while not stop_event.is_set():
                    try:
                        data, _ = s.recvfrom(256)
                        msg = json.loads(data)
                        if msg.get("a") == "sw" and msg.get("id") != _MACHINE_ID:
                            host = int(msg["h"])
                            log.info("📡 Sync reçu : switch → hôte %d", host + 1)
                            callback(host)
                    except TimeoutError:
                        continue
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                        continue
        except OSError as exc:
            log.warning("📡 Sync listener erreur (port %d) : %s", port, exc)

    t = threading.Thread(target=_listen, name="sync-listener", daemon=True)
    t.start()
    return t
