"""Tests pour swigi.path_push — watcher Gen S (notification CHANGE_HOST)."""

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

from swigi.path_push import _drain_switch, watch_keyboard_push
from swigi.state import _SwitchEvent
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


def _ping_response():
    """Paquet HID++ réponse ping valide (feature_index=0x00)."""
    return bytes([0x11, 0xFF, 0x00, 0x0A] + [0] * 16)


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

    def test_short_packet_continues_drain(self):
        """Paquet court → continuer la lecture (ne pas stopper le drain)."""
        keyboard = _make_keyboard()
        short_packet = bytes([0x11, 0xFF, 5])  # trop court
        valid_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        keyboard.transport.read.side_effect = [short_packet, valid_packet] + [None] * 8
        result = _drain_switch(keyboard)
        self.assertEqual(result, 2)


@patch("swigi.path_push.get_current_host", return_value=0)
class TestWatchKeyboardPush(unittest.TestCase):
    def test_notification_posts_switch_event(self, mock_get_host):
        """Notification CHANGE_HOST + ping timeout → _SwitchEvent posté."""
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
            # Ping check: timeout → is_stale=False → switch réel
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
        """Même target < debounce → un seul event."""
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
            if calls[0] in (1, 3):
                return packet  # notification (call 1) + ping-read (call 3 returns notif, not stale)
            if calls[0] == 2:
                return None  # ping check after call 1 → not stale → emit
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ), patch("swigi.path_push._DEBOUNCE", 0.5):
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

    @patch("swigi.path_push._reconnect_keyboard")
    def test_drain_on_disconnect(self, mock_reconnect, mock_get_host):
        """Write fail → drain capture notification buffered → SwitchEvent."""
        keyboard = _make_keyboard()
        mock_reconnect.return_value = None

        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        keyboard.transport.write.side_effect = TransportError("dead")
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        keyboard.transport.read.side_effect = [packet] + [None] * 10

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.01
        ), patch("swigi.state._STABILITY_WAIT", 0.0):
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
        push_events = [e for e in events if e.source == "push"]
        self.assertGreaterEqual(len(push_events), 1)
        self.assertEqual(push_events[0].target_host, 2)

    @patch("swigi.path_push._reconnect_keyboard")
    def test_reconnect_posts_no_pull_event(self, mock_reconnect, mock_get_host):
        """Reconnexion Gen S → aucun SwitchEvent : seul Easy-Switch déclenche les switchs."""
        keyboard = _make_keyboard()
        new_keyboard = _make_keyboard(name="MX Keys Mini (new)")
        new_keyboard.transport.read.return_value = None
        mock_reconnect.return_value = new_keyboard

        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

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
        ), patch("swigi.state._STABILITY_WAIT", 0.0):
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
        self.assertEqual(len(pull_events), 0)

    @patch("swigi.path_push._reconnect_keyboard")
    def test_watchdog_triggers_reconnect(self, mock_reconnect, mock_get_host):
        """10s sans réponse → watchdog → reconnexion."""
        keyboard = _make_keyboard()
        mock_reconnect.return_value = None

        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
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

    @patch("swigi.path_push._reconnect_keyboard")
    def test_stale_notification_filtered_by_ping(self, mock_reconnect, mock_get_host):
        """Notification stale → ping OK (keyboard répond) → ignorée."""
        keyboard = _make_keyboard()
        new_keyboard = _make_keyboard(name="MX Keys Mini (new)")
        mock_reconnect.return_value = new_keyboard

        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # Clavier déconnecte → drain capture switch target=2
        keyboard.transport.write.side_effect = TransportError("dead")
        drain_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        keyboard.transport.read.side_effect = [drain_packet] + [None] * 10

        # Après reconnexion : notification stale target=2, puis ping répond → stale
        stale_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        new_calls = [0]

        def new_read(timeout=10):
            new_calls[0] += 1
            if new_calls[0] == 1:
                return stale_packet  # CHANGE_HOST notification
            if new_calls[0] == 2:
                return _ping_response()  # keyboard stable → stale
            stop_event.set()
            return None

        new_keyboard.transport.read.side_effect = new_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.01
        ), patch("swigi.state._STABILITY_WAIT", 0.0):
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
        push_events = [e for e in events if e.target_host == 2]
        self.assertEqual(len(push_events), 1, "stale notification must be filtered by ping")

    @patch("swigi.path_push._reconnect_keyboard")
    def test_local_host_notification_filtered(self, mock_reconnect, mock_get_host):
        """Notification firmware 'hôte local' (target == this_mac_host) → ignorée."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        local_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 0] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return local_packet
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
            thread.join(timeout=2.0)

        self.assertTrue(event_queue.empty(), "local-host notification must not fire event")


class TestStaleDetection(unittest.TestCase):
    """Détection stale : ping actif DANS la fenêtre post-reconnect, pas hors fenêtre."""

    @patch("swigi.path_push.get_current_host", return_value=0)
    def test_stale_in_window_dropped_by_ping(self, mock_get_host):
        """Mac receveur (fresh connect) : notification dans fenêtre + ping OK phase1+phase2 → droppée."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {"keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        stale_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return stale_packet
            if calls[0] == 2:
                return _ping_response()  # phase 1 : keyboard répond → ambigu
            if calls[0] == 3:
                return _ping_response()  # phase 2 : keyboard répond encore → stale confirmé
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        # _STALE_CONFIRM_WAIT=0.0 pour éviter 200ms de sleep dans les tests
        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ), patch("swigi.path_push._RECONNECT_STALE_WINDOW", 10.0), patch(
            "swigi.path_push._STALE_CONFIRM_WAIT", 0.0
        ):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertTrue(event_queue.empty(), "notification stale doit être droppée (ping OK phase1+phase2)")

    @patch("swigi.path_push.get_current_host", return_value=0)
    def test_real_switch_in_window_accepted_two_phase(self, mock_get_host):
        """Mac source : switch dans fenêtre, phase1 ping répond (Gen S pre-disconnect), phase2 timeout → accepté."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {"keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        valid_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return valid_packet
            if calls[0] == 2:
                return _ping_response()  # phase 1 : clavier répond encore (Gen S pre-disconnect)
            if calls[0] == 3:
                return None  # phase 2 : clavier déco BT → switch réel confirmé
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.1
        ), patch("swigi.path_push._RECONNECT_STALE_WINDOW", 10.0), patch(
            "swigi.path_push._STALE_CONFIRM_WAIT", 0.0
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

        self.assertEqual(event.target_host, 2)
        self.assertEqual(event.source, "push")

    @patch("swigi.path_push.get_current_host", return_value=0)
    def test_real_switch_in_window_accepted_ping_timeout(self, mock_get_host):
        """Mac receveur : switch dans fenêtre + ping timeout (déco) → accepté."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {"keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        valid_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return valid_packet
            if calls[0] == 2:
                return None  # ping timeout → déconnexion → switch réel
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.1
        ), patch("swigi.path_push._RECONNECT_STALE_WINDOW", 10.0):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            event = event_queue.get(timeout=2.0)
            stop_event.set()
            thread.join(timeout=2.0)

        self.assertEqual(event.target_host, 2)
        self.assertEqual(event.source, "push")

    @patch("swigi.path_push.get_current_host", return_value=0)
    def test_source_mac_no_ping_outside_window(self, mock_get_host):
        """Mac source (clavier connecté depuis longtemps) : notification acceptée sans ping."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {"keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # target=2, this_mac_host=0 → pas filtre 1
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return packet
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        # _RECONNECT_STALE_WINDOW=0 → fenêtre expirée → pas de ping → notification directe
        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ), patch("swigi.path_push._RECONNECT_STALE_WINDOW", 0.0):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            event = event_queue.get(timeout=2.0)
            stop_event.set()
            thread.join(timeout=2.0)

        self.assertEqual(event.target_host, 2)
        # Ping ne doit PAS avoir été appelé (write appelé une seule fois : le keepalive)
        # On vérifie juste que l'event est émis correctement


class TestSwIdFilter(unittest.TestCase):
    """raw[3] sw_id filtre : notifications firmware (sw_id=0) vs réponses requêtes (sw_id!=0)."""

    @patch("swigi.path_push.get_current_host", return_value=0)
    @patch("swigi.path_push._reconnect_keyboard")
    @patch("swigi.path_push._set_keyboard_status")
    def test_push_ignores_non_notification_packets(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        """raw[3]=0x0A (sw_id != 0) → aucun SwitchEvent posté."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        packet_non_notif = bytes([0x11, 0xFF, 5, 0x0A, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=50):
            calls[0] += 1
            if calls[0] == 1:
                return packet_non_notif
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read
        keyboard.transport.write.return_value = None

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.1
        ), patch("swigi.path_push._DEBOUNCE", 0.1):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertTrue(event_queue.empty(), "SwitchEvent posté pour paquet non-notification")

    @patch("swigi.path_push.get_current_host", return_value=0)
    @patch("swigi.path_push._reconnect_keyboard")
    @patch("swigi.path_push._set_keyboard_status")
    def test_push_accepts_notification_packets(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        """raw[3]=0x00 (sw_id=0, notification firmware) + ping timeout → SwitchEvent posté."""
        keyboard = _make_keyboard()
        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        packet_notif = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=50):
            calls[0] += 1
            if calls[0] == 1:
                return packet_notif
            if calls[0] == 2:
                return None  # ping timeout → switch réel
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read
        keyboard.transport.write.return_value = None

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.2
        ), patch("swigi.path_push._DEBOUNCE", 0.1):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertFalse(event_queue.empty(), "Aucun SwitchEvent pour paquet notification")


if __name__ == "__main__":
    unittest.main()
