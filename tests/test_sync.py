"""Tests pour swigi.sync — broadcast UDP inter-Mac."""

import json
import queue
import socket
import threading
import time
import unittest
from unittest.mock import patch


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TestBroadcastSwitch(unittest.TestCase):
    def test_sends_correct_payload(self):
        """broadcast_switch envoie payload JSON correct avec action, host, machine_id."""
        from swigi.sync import _MACHINE_ID, broadcast_switch

        captured = []

        class _FakeSocket:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def setsockopt(self, *a): pass
            def settimeout(self, *a): pass
            def sendto(self, data, addr):
                captured.append((json.loads(data), addr))

        with patch("swigi.sync.socket.socket", return_value=_FakeSocket()):
            broadcast_switch(2)

        self.assertEqual(len(captured), 1)
        msg, (addr, _port) = captured[0]
        self.assertEqual(msg["action"], "switch_mouse")
        self.assertEqual(msg["host"], 2)
        self.assertEqual(msg["machine_id"], _MACHINE_ID)
        self.assertEqual(addr, "255.255.255.255")

    def test_handles_send_error_silently(self):
        """Erreur d'envoi UDP loggée en debug, pas d'exception levée."""
        from swigi.sync import broadcast_switch

        with patch("swigi.sync.socket.socket") as mock_socket:
            mock_socket.return_value.__enter__.return_value.sendto.side_effect = OSError("fail")
            broadcast_switch(1)  # ne doit pas lever


class TestSyncListener(unittest.TestCase):
    """Envoie directement à 127.0.0.1 sur un port aléatoire pour éviter les conflits."""

    def test_listener_calls_callback(self):
        """Listener appelle callback avec le bon host sur réception broadcast étranger."""
        from swigi.sync import start_sync_listener

        port = _free_port()
        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event, port=port)
        time.sleep(0.05)

        msg = json.dumps({"action": "switch_mouse", "host": 2, "machine_id": "foreign-mac"}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", port))

        try:
            host = results.get(timeout=2.0)
            self.assertEqual(host, 2)
        finally:
            stop_event.set()

    def test_listener_ignores_self(self):
        """Listener ignore les broadcasts avec son propre machine ID."""
        from swigi.sync import _MACHINE_ID, start_sync_listener

        port = _free_port()
        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event, port=port)
        time.sleep(0.05)

        msg = json.dumps({"action": "switch_mouse", "host": 1, "machine_id": _MACHINE_ID}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", port))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "broadcast propre ne doit pas déclencher callback")

    def test_listener_ignores_invalid_json(self):
        """Paquet JSON invalide ignoré sans crash."""
        from swigi.sync import start_sync_listener

        port = _free_port()
        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event, port=port)
        time.sleep(0.05)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"not-json!!!", ("127.0.0.1", port))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "paquet invalide ne doit pas déclencher callback")

    def test_listener_ignores_wrong_action(self):
        """Paquet avec action inconnue ignoré."""
        from swigi.sync import start_sync_listener

        port = _free_port()
        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event, port=port)
        time.sleep(0.05)

        msg = json.dumps({"action": "ping", "host": 0, "machine_id": "other"}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", port))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "action inconnue ne doit pas déclencher callback")

    def test_listener_ignores_out_of_range_host(self):
        """Host hors plage valide (0-7) ignoré."""
        from swigi.sync import start_sync_listener

        port = _free_port()
        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event, port=port)
        time.sleep(0.05)

        msg = json.dumps({"action": "switch_mouse", "host": 99, "machine_id": "other"}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", port))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "host hors plage ne doit pas déclencher callback")


if __name__ == "__main__":
    unittest.main()
