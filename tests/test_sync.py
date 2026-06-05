"""Tests pour swigi.sync — broadcast UDP inter-Mac."""

import json
import queue
import socket
import threading
import time
import unittest
from unittest.mock import patch


class TestBroadcastSwitch(unittest.TestCase):
    def test_sends_udp_packet(self):
        """broadcast_switch envoie un paquet UDP avec action et host corrects."""
        from swigi.sync import _MACHINE_ID, SYNC_PORT, broadcast_switch

        received = queue.Queue()

        def _listen():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("", SYNC_PORT))
                s.settimeout(2.0)
                try:
                    data, _ = s.recvfrom(256)
                    received.put(json.loads(data))
                except TimeoutError:
                    pass

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
        time.sleep(0.05)  # laisse le socket se lier

        broadcast_switch(2)
        t.join(timeout=3.0)

        self.assertFalse(received.empty(), "aucun paquet UDP reçu")
        msg = received.get_nowait()
        self.assertEqual(msg["a"], "sw")
        self.assertEqual(msg["h"], 2)
        self.assertEqual(msg["id"], _MACHINE_ID)

    def test_handles_send_error_silently(self):
        """Erreur d'envoi UDP loggée en debug, pas d'exception levée."""
        from swigi.sync import broadcast_switch

        with patch("swigi.sync.socket.socket") as mock_socket:
            mock_socket.return_value.__enter__.return_value.sendto.side_effect = OSError("fail")
            broadcast_switch(1)  # ne doit pas lever


class TestSyncListener(unittest.TestCase):
    def test_listener_calls_callback(self):
        """Listener appelle callback avec le bon host sur réception broadcast étranger."""
        from swigi.sync import SYNC_PORT, start_sync_listener

        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event)
        time.sleep(0.05)

        # Envoyer depuis une machine avec ID différent
        foreign_id = "foreign-machine-000"
        msg = json.dumps({"a": "sw", "h": 2, "id": foreign_id}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(msg, ("127.0.0.1", SYNC_PORT))

        try:
            host = results.get(timeout=2.0)
            self.assertEqual(host, 2)
        finally:
            stop_event.set()

    def test_listener_ignores_self(self):
        """Listener ignore les broadcasts avec son propre machine ID."""
        from swigi.sync import _MACHINE_ID, SYNC_PORT, start_sync_listener

        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event)
        time.sleep(0.05)

        msg = json.dumps({"a": "sw", "h": 1, "id": _MACHINE_ID}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", SYNC_PORT))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "broadcast propre ne doit pas déclencher callback")

    def test_listener_ignores_invalid_json(self):
        """Paquet JSON invalide ignoré sans crash."""
        from swigi.sync import SYNC_PORT, start_sync_listener

        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event)
        time.sleep(0.05)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"not-json!!!", ("127.0.0.1", SYNC_PORT))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "paquet invalide ne doit pas déclencher callback")

    def test_listener_ignores_wrong_action(self):
        """Paquet avec action inconnue ignoré."""
        from swigi.sync import SYNC_PORT, start_sync_listener

        stop_event = threading.Event()
        results = queue.Queue()
        start_sync_listener(results.put, stop_event)
        time.sleep(0.05)

        msg = json.dumps({"a": "ping", "h": 0, "id": "other"}).encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", SYNC_PORT))

        time.sleep(0.2)
        stop_event.set()
        self.assertTrue(results.empty(), "action inconnue ne doit pas déclencher callback")


if __name__ == "__main__":
    unittest.main()
