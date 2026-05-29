"""Tests pour swigi.path_pull — watcher Legacy (reconnect-only PULL)."""

import queue
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

if "swigi.hidapi_loader" not in sys.modules:
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    sys.modules["swigi.hidapi_loader"] = _mock_loader

if "swigi.gui" not in sys.modules:
    _mock_gui = MagicMock()
    _mock_gui.notify = MagicMock()
    _mock_gui.prefs = {"mouse_follow": True}
    _mock_gui._prefs_lock = threading.Lock()
    sys.modules["swigi.gui"] = _mock_gui

from swigi.path_pull import watch_keyboard_pull
from swigi.transport import TransportError


def _make_keyboard(
    name="MX Keys Wireless", product_id=0xB35B, change_host_index=5
):
    device = MagicMock()
    device.name = name
    device.product_id = product_id
    device.change_host_index = change_host_index
    device.generation = "pull"
    device.transport = MagicMock()
    device.transport.is_open = True

    def _close():
        device.transport.is_open = False

    device.close.side_effect = _close
    return device


@patch("swigi.path_pull.get_current_host", return_value=0)
class TestWatchKeyboardPull(unittest.TestCase):
    @patch("swigi.daemon.get_current_host", return_value=2)
    @patch("swigi.daemon._reconnect_keyboard")
    def test_reconnect_posts_pull_event(self, mock_reconnect, mock_daemon_host, mock_get_host):
        """Déconnexion → reconnexion → _SwitchEvent(this_mac_host, source='pull')."""
        keyboard = _make_keyboard()
        new_keyboard = _make_keyboard(name="MX Keys Wireless (new)")
        mock_reconnect.return_value = new_keyboard

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # First ping fails → disconnect
        call_count = [0]

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TransportError("dead")
            # After reconnect, next write succeeds

        keyboard.transport.write.side_effect = write_side_effect

        # After reconnect, stop on second ping
        new_calls = [0]

        def new_write(data):
            new_calls[0] += 1
            if new_calls[0] >= 2:
                stop_event.set()

        new_keyboard.transport.write.side_effect = new_write

        with patch("swigi.path_pull._PING_INTERVAL", 0.01), patch(
            "swigi.daemon._STABILITY_WAIT", 0.0
        ):
            thread = threading.Thread(
                target=watch_keyboard_pull,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].source, "pull")
        self.assertEqual(events[0].target_host, 2)

    def test_no_hid_read_in_connected_loop(self, mock_get_host):
        """Le watcher PULL ne fait PAS de transport.read() (pas de notification)."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        call_count = [0]

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] >= 3:
                stop_event.set()

        keyboard.transport.write.side_effect = write_side_effect

        with patch("swigi.path_pull._PING_INTERVAL", 0.01):
            thread = threading.Thread(
                target=watch_keyboard_pull,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        # Verify: transport.read was NOT called (no HID++ reading in PULL path)
        keyboard.transport.read.assert_not_called()

    @patch("swigi.daemon._reconnect_keyboard")
    def test_watchdog_triggers_reconnect(self, mock_reconnect, mock_get_host):
        """Watchdog → reconnexion quand pas de réponse."""
        keyboard = _make_keyboard()
        mock_reconnect.return_value = None  # reconnect fails → exit

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # Ping succeeds but time shows watchdog expired
        keyboard.transport.write.return_value = None
        base = 1000.0
        call_n = [0]

        def fake_time():
            call_n[0] += 1
            if call_n[0] <= 2:
                return base
            return base + 15.0

        with patch("swigi.path_pull.time.time", side_effect=fake_time), patch(
            "swigi.path_pull.time.sleep"
        ):
            thread = threading.Thread(
                target=watch_keyboard_pull,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        mock_reconnect.assert_called_once()

    def test_stop_event_exits_cleanly(self, mock_get_host):
        """stop_event.set() → thread termine proprement."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        call_count = [0]

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] >= 2:
                stop_event.set()

        keyboard.transport.write.side_effect = write_side_effect

        with patch("swigi.path_pull._PING_INTERVAL", 0.01):
            thread = threading.Thread(
                target=watch_keyboard_pull,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
