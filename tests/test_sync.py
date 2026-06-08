"""Tests pour swigi.sync — coordination inter-Mac via UDP broadcast."""

import json
import socket
import threading
import time
import unittest
from unittest.mock import patch


class TestBroadcastSwitch(unittest.TestCase):
    def test_sends_correct_payload(self):
        """broadcast_switch envoie JSON {target, from} en UDP broadcast."""
        sent = []

        class _FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def setsockopt(self, *a): pass
            def sendto(self, data, addr):
                sent.append((data, addr))

        with patch("swigi.sync.socket.socket", return_value=_FakeSocket()):
            from swigi.sync import _MACHINE_ID, _PORT, broadcast_switch
            broadcast_switch(1)

        self.assertEqual(len(sent), 1)
        payload, addr = sent[0]
        msg = json.loads(payload)
        self.assertEqual(msg["target"], 1)
        self.assertEqual(msg["from"], _MACHINE_ID)
        self.assertEqual(addr, ("255.255.255.255", _PORT))

    def test_oserror_silenced(self):
        """broadcast_switch ne crash pas sur erreur réseau."""
        with patch("swigi.sync.socket.socket", side_effect=OSError("no network")):
            from swigi.sync import broadcast_switch
            broadcast_switch(0)  # doit passer silencieusement


class TestSyncListener(unittest.TestCase):
    def _make_foreign_msg(self, target: int) -> bytes:
        return json.dumps({"target": target, "from": "other-machine-id"}).encode()

    def test_calls_callback_on_foreign_message(self):
        """Listener appelle callback avec le target_host reçu."""
        received = []
        stop = threading.Event()

        from swigi.sync import _PORT, start_sync_listener

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            t = start_sync_listener(received.append, stop)
            time.sleep(0.05)
            sender.sendto(self._make_foreign_msg(2), ("127.0.0.1", _PORT))
            time.sleep(0.1)

        stop.set()
        t.join(timeout=2.0)
        self.assertIn(2, received)

    def test_ignores_own_broadcast(self):
        """Listener ignore les messages provenant de ce Mac (anti-boucle)."""
        received = []
        stop = threading.Event()

        from swigi.sync import _MACHINE_ID, _PORT, start_sync_listener

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            t = start_sync_listener(received.append, stop)
            time.sleep(0.05)
            own_msg = json.dumps({"target": 0, "from": _MACHINE_ID}).encode()
            sender.sendto(own_msg, ("127.0.0.1", _PORT))
            time.sleep(0.1)

        stop.set()
        t.join(timeout=2.0)
        self.assertEqual(received, [])

    def test_ignores_malformed_json(self):
        """Listener ignore JSON malformé sans crash."""
        received = []
        stop = threading.Event()

        from swigi.sync import _PORT, start_sync_listener

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            t = start_sync_listener(received.append, stop)
            time.sleep(0.05)
            sender.sendto(b"not-json", ("127.0.0.1", _PORT))
            time.sleep(0.1)

        stop.set()
        t.join(timeout=2.0)
        self.assertEqual(received, [])

    def test_stops_on_event(self):
        """Listener s'arrête proprement quand stop_event est set."""
        stop = threading.Event()
        from swigi.sync import start_sync_listener
        t = start_sync_listener(lambda _: None, stop)
        stop.set()
        t.join(timeout=3.0)
        self.assertFalse(t.is_alive())


if __name__ == "__main__":
    unittest.main()
