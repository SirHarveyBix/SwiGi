"""Tests pour swigi.path_push — watcher Gen S (notification PUSH + PULL fallback)."""

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

from swigi.daemon import _SwitchEvent
from swigi.path_push import _drain_switch, watch_keyboard_push
from swigi.transport import TransportError


def _make_keyboard(name="MX Keys Mini", product_id=0xB369, change_host_index=5):
    device = MagicMock()
    device.name = name
    device.product_id = product_id
    device.change_host_index = change_host_index
    device.generation = "push"
    device.transport = MagicMock()
    device.transport.is_open = True

    def _close():
        device.transport.is_open = False

    device.close.side_effect = _close
    return device


class TestDrainSwitch(unittest.TestCase):
    def test_captures_switch_in_buffer(self):
        """Capture une notification CHANGE_HOST dans le buffer."""
        keyboard = _make_keyboard()
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        keyboard.transport.read.return_value = packet
        result = _drain_switch(keyboard)
        self.assertEqual(result, 1)

    def test_returns_none_if_no_switch(self):
        """Retourne None si aucune notification trouvée."""
        keyboard = _make_keyboard()
        keyboard.transport.read.return_value = None
        result = _drain_switch(keyboard)
        self.assertIsNone(result)

    def test_stops_on_transport_error(self):
        """Arrête sur TransportError."""
        keyboard = _make_keyboard()
        keyboard.transport.read.side_effect = TransportError("dead")
        result = _drain_switch(keyboard)
        self.assertIsNone(result)

    def test_invalid_target_ignored(self):
        """Target hors limites → None."""
        keyboard = _make_keyboard()
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 5] + [0] * 14)
        keyboard.transport.read.side_effect = [packet] + [None] * 10
        result = _drain_switch(keyboard)
        self.assertIsNone(result)


@patch("swigi.path_push.get_current_host", return_value=0)
class TestWatchKeyboardPush(unittest.TestCase):
    def test_notification_posts_switch_event(self, mock_get_host):
        """Notification CHANGE_HOST → _SwitchEvent posté avec source='push'."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return packet
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            event = event_queue.get(timeout=2.0)
            stop_event.set()
            thread.join(timeout=2.0)

        self.assertIsInstance(event, _SwitchEvent)
        self.assertEqual(event.target_host, 2)
        self.assertEqual(event.source, "push")

    def test_debounce_same_target(self, mock_get_host):
        """Même target < 1s → un seul event."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] <= 3:
                return packet
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ), patch("swigi.path_push._DEBOUNCE", 0.1):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        self.assertEqual(len(events), 1)

    @patch("swigi.daemon._reconnect_keyboard")
    def test_drain_on_disconnect(self, mock_reconnect, mock_get_host):
        """Write fail → drain capture notification buffered → SwitchEvent."""
        keyboard = _make_keyboard()
        mock_reconnect.return_value = None

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        keyboard.transport.write.side_effect = TransportError("dead")
        # Drain captures a switch notification
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        keyboard.transport.read.side_effect = [packet] + [None] * 10

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.01
        ), patch("swigi.daemon._STABILITY_WAIT", 0.0):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        # Should have captured switch via drain
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        push_events = [e for e in events if e.source == "push"]
        self.assertGreaterEqual(len(push_events), 1)
        self.assertEqual(push_events[0].target_host, 2)

    @patch("swigi.daemon.get_current_host", return_value=1)
    @patch("swigi.daemon._reconnect_keyboard")
    def test_reconnect_posts_pull_event(self, mock_reconnect, mock_daemon_host, mock_get_host):
        """Reconnexion → _SwitchEvent avec source='pull' (fallback PULL)."""
        keyboard = _make_keyboard()
        new_keyboard = _make_keyboard(name="MX Keys Mini (new)")
        new_keyboard.transport.read.return_value = None
        mock_reconnect.return_value = new_keyboard

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # First write fails → disconnect → reconnect
        keyboard.transport.write.side_effect = TransportError("dead")
        keyboard.transport.read.return_value = None

        calls = [0]

        def new_read(timeout=10):
            calls[0] += 1
            if calls[0] > 1:
                stop_event.set()
            return None

        new_keyboard.transport.read.side_effect = new_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.01
        ), patch("swigi.daemon._STABILITY_WAIT", 0.0):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        pull_events = [e for e in events if e.source == "pull"]
        self.assertGreaterEqual(len(pull_events), 1)
        self.assertEqual(pull_events[0].target_host, 1)

    @patch("swigi.daemon._reconnect_keyboard")
    def test_watchdog_triggers_reconnect(self, mock_reconnect, mock_get_host):
        """10s sans réponse → watchdog → reconnexion."""
        keyboard = _make_keyboard()
        mock_reconnect.return_value = None

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        keyboard.transport.write.return_value = None
        keyboard.transport.read.return_value = None

        base = 1000.0
        call_n = [0]

        def fake_time():
            call_n[0] += 1
            if call_n[0] == 1:
                return base
            return base + 15.0

        with patch("swigi.path_push.time.time", side_effect=fake_time), patch(
            "swigi.path_push.time.sleep"
        ):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        mock_reconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
