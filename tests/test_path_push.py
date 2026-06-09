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

from swigi.path_push import (
    _drain_switch,
    _emit_arrival_switch,
    _is_stale_notification,
    _restore_backlight,
    _save_initial_backlight,
    watch_keyboard_push,
)
from swigi.state import _SwitchEvent
from swigi.transport import TransportError


def _make_keyboard(name="MX Keys Mini", product_id=0xB369, change_host_index=5):
    device = MagicMock()
    device.name = name
    device.product_id = product_id
    device.change_host_index = change_host_index
    device.backlight_index = None
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


@patch("swigi.path_push.get_host_info", return_value=(3, 0))
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
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].target_host, 2)

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
        self.assertEqual(len(events), 0)

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

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
    def test_stale_in_window_dropped_by_ping(self, mock_get_host):
        """Mac receveur (fresh connect) : notification dans fenêtre + ping OK initial+2polls → droppée."""
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
            if calls[0] in (2, 3, 4):
                return _ping_response()  # initial + 2 polls → clavier répond = stale confirmé
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        # _STALE_MAX_PINGS=2, _STALE_POLL_INTERVAL=0.0 pour tests rapides
        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ), patch("swigi.path_push._RECONNECT_STALE_WINDOW", 10.0), patch(
            "swigi.path_push._STALE_MAX_PINGS", 2
        ), patch("swigi.path_push._STALE_POLL_INTERVAL", 0.0):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertTrue(event_queue.empty(), "notification stale doit être droppée (ping OK initial+2polls)")

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
    def test_real_switch_in_window_accepted_two_phase(self, mock_get_host):
        """Mac source : switch dans fenêtre, ping initial répond (MX Keys S pre-disconnect), poll1 timeout → accepté."""
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
                return _ping_response()  # ping initial : clavier encore connecté (Gen S pre-disconnect)
            if calls[0] == 3:
                return None  # poll 1 : clavier déco BLE → switch réel confirmé
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.1
        ), patch("swigi.path_push._RECONNECT_STALE_WINDOW", 10.0), patch(
            "swigi.path_push._STALE_MAX_PINGS", 2
        ), patch("swigi.path_push._STALE_POLL_INTERVAL", 0.0):
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

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
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

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
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

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
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

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
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


class TestBacklightPerKeyboard(unittest.TestCase):
    """backlight_dirty_{pid} : flag per-keyboard — MX Mini ne vole pas le flag de MX Keys S."""

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
    @patch("swigi.path_push.set_backlight_config", return_value=True)
    @patch("swigi.path_push.get_backlight_config", return_value=(75, 0, 0))
    def test_keys_s_restores_backlight_on_dirty_flag(
        self, mock_get_bl, mock_set_bl, mock_get_host
    ):
        """MX Keys S (backlight_index != None) restaure le backlight quand son flag est posé."""
        from swigi.prefs import prefs
        keys_s = _make_keyboard(name="MX Keys S", product_id=0xB35B, change_host_index=5)
        keys_s.backlight_index = 7
        prefs[f"backlight_{keys_s.product_id}"] = 75

        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {keys_s.product_id: {"name": keys_s.name, "ok": True}},
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                state[f"backlight_dirty_{keys_s.product_id}"] = True
                return None
            stop_event.set()
            return None

        keys_s.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 1000.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keys_s, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        mock_set_bl.assert_called()
        self.assertNotIn(f"backlight_dirty_{keys_s.product_id}", state)

    @patch("swigi.path_push.get_host_info", return_value=(3, 0))
    @patch("swigi.path_push.set_backlight_config", return_value=True)
    def test_mx_mini_does_not_consume_keys_s_flag(self, mock_set_bl, mock_get_host):
        """MX Mini (pas de backlight) n'efface PAS le flag backlight_dirty du MX Keys S."""
        keys_s_pid = 0xB35B
        mini = _make_keyboard(name="MX Keys Mini", product_id=0xB369, change_host_index=5)
        mini.backlight_index = None

        event_queue = queue.Queue()
        state = {
            "_lock": threading.Lock(),
            "keyboards": {mini.product_id: {"name": mini.name, "ok": True}},
            f"backlight_dirty_{keys_s_pid}": True,
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] >= 2:
                stop_event.set()
            return None

        mini.transport.read.side_effect = mock_read

        with patch("swigi.path_push._PING_INTERVAL", 1000.0), patch(
            "swigi.path_push._READ_WINDOW", 0.05
        ):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(mini, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertTrue(
            state.get(f"backlight_dirty_{keys_s_pid}"),
            "MX Mini a consommé le flag backlight du MX Keys S — BUG",
        )
        mock_set_bl.assert_not_called()


# ── Tests helpers backlight ────────────────────────────────────────────────────


class TestSaveInitialBacklight(unittest.TestCase):
    def _make_keyboard_with_backlight(self, product_id=0xB35B):
        kb = _make_keyboard(product_id=product_id)
        kb.backlight_index = 7
        return kb

    @patch("swigi.path_push.save_prefs")
    @patch("swigi.path_push.get_backlight_config", return_value=(80, 0, 0))
    def test_saves_level_when_key_absent(self, mock_get_bl, mock_save):
        """Sauvegarde le level initial quand la clé est absente des prefs."""
        kb = self._make_keyboard_with_backlight(product_id=0xAAAA)
        fresh_prefs = {}
        with patch("swigi.path_push.prefs", fresh_prefs):
            _save_initial_backlight(kb)
        self.assertEqual(fresh_prefs.get(f"backlight_{kb.product_id}"), 80)
        mock_save.assert_called_once()

    @patch("swigi.path_push.save_prefs")
    @patch("swigi.path_push.get_backlight_config", return_value=None)
    def test_no_save_when_config_returns_none(self, mock_get_bl, mock_save):
        """Pas de sauvegarde si get_backlight_config retourne None."""
        kb = self._make_keyboard_with_backlight(product_id=0xBBBB)
        fresh_prefs = {}
        with patch("swigi.path_push.prefs", fresh_prefs):
            _save_initial_backlight(kb)
        mock_save.assert_not_called()

    @patch("swigi.path_push.save_prefs")
    @patch("swigi.path_push.get_backlight_config", side_effect=TransportError("dead"))
    def test_transport_error_silenced(self, mock_get_bl, mock_save):
        """TransportError dans get_backlight_config ne propage pas."""
        kb = self._make_keyboard_with_backlight(product_id=0xCCCC)
        fresh_prefs = {}
        with patch("swigi.path_push.prefs", fresh_prefs):
            _save_initial_backlight(kb)  # ne doit pas lever
        mock_save.assert_not_called()

    def test_skips_when_backlight_index_none(self):
        """backlight_index=None → retour immédiat, rien n'est appelé."""
        kb = _make_keyboard(product_id=0xDDDD)
        kb.backlight_index = None
        with patch("swigi.path_push.get_backlight_config") as mock_get:
            _save_initial_backlight(kb)
        mock_get.assert_not_called()

    @patch("swigi.path_push.save_prefs")
    @patch("swigi.path_push.get_backlight_config")
    def test_skips_when_key_already_in_prefs(self, mock_get, mock_save):
        """Clé déjà dans les prefs → get_backlight_config non appelé."""
        kb = self._make_keyboard_with_backlight(product_id=0xEEEE)
        existing_prefs = {f"backlight_{kb.product_id}": 50}
        with patch("swigi.path_push.prefs", existing_prefs):
            _save_initial_backlight(kb)
        mock_get.assert_not_called()


class TestRestoreBacklight(unittest.TestCase):
    def _make_keyboard_with_backlight(self, product_id=0xB35B):
        kb = _make_keyboard(product_id=product_id)
        kb.backlight_index = 7
        return kb

    @patch("swigi.path_push.set_backlight_config", return_value=True)
    def test_restores_level_when_saved(self, mock_set):
        """Restaure le level quand la clé est dans les prefs."""
        kb = self._make_keyboard_with_backlight(product_id=0xF001)
        prefs_with_level = {f"backlight_{kb.product_id}": 75}
        with patch("swigi.path_push.prefs", prefs_with_level):
            _restore_backlight(kb)
        mock_set.assert_called_once()

    @patch("swigi.path_push.set_backlight_config", return_value=False)
    def test_no_reply_logged_not_raised(self, mock_set):
        """set_backlight_config retourne False → loggé, pas d'exception."""
        kb = self._make_keyboard_with_backlight(product_id=0xF002)
        prefs_with_level = {f"backlight_{kb.product_id}": 50}
        with patch("swigi.path_push.prefs", prefs_with_level):
            _restore_backlight(kb)  # ne doit pas lever
        mock_set.assert_called_once()

    @patch("swigi.path_push.set_backlight_config", side_effect=TransportError("dead"))
    def test_transport_error_silenced(self, mock_set):
        """TransportError dans set_backlight_config ne propage pas."""
        kb = self._make_keyboard_with_backlight(product_id=0xF003)
        prefs_with_level = {f"backlight_{kb.product_id}": 60}
        with patch("swigi.path_push.prefs", prefs_with_level):
            _restore_backlight(kb)  # ne doit pas lever

    def test_skips_when_level_none(self):
        """Pas de level dans les prefs → set_backlight_config non appelé."""
        kb = self._make_keyboard_with_backlight(product_id=0xF004)
        empty_prefs = {}
        with patch("swigi.path_push.prefs", empty_prefs), \
             patch("swigi.path_push.set_backlight_config") as mock_set:
            _restore_backlight(kb)
        mock_set.assert_not_called()

    def test_skips_when_backlight_index_none(self):
        """backlight_index=None → retour immédiat."""
        kb = _make_keyboard(product_id=0xF005)
        kb.backlight_index = None
        with patch("swigi.path_push.set_backlight_config") as mock_set:
            _restore_backlight(kb)
        mock_set.assert_not_called()


# ── Tests _is_stale_notification ──────────────────────────────────────────────


class TestIsStaleNotification(unittest.TestCase):
    def test_returns_true_when_ping_response_valid(self):
        """Ping répondu correctement → clavier stable → True."""
        from swigi.constants import SW_ID
        kb = _make_keyboard()
        kb.transport.write.return_value = None
        kb.transport.read.return_value = bytes([0x11, 0xFF, 0x00, SW_ID] + [0] * 16)
        self.assertTrue(_is_stale_notification(kb))

    def test_returns_false_when_no_response(self):
        """Pas de réponse au ping → déconnexion → False."""
        kb = _make_keyboard()
        kb.transport.write.return_value = None
        kb.transport.read.return_value = None
        self.assertFalse(_is_stale_notification(kb))

    def test_returns_false_on_transport_error(self):
        """TransportError pendant le ping → False (clavier déconnecté)."""
        kb = _make_keyboard()
        kb.transport.write.side_effect = TransportError("dead")
        self.assertFalse(_is_stale_notification(kb))

    def test_returns_false_on_os_error(self):
        """OSError pendant read → False."""
        kb = _make_keyboard()
        kb.transport.write.return_value = None
        kb.transport.read.side_effect = OSError("io error")
        self.assertFalse(_is_stale_notification(kb))


# ── Tests _drain_switch ligne 101 ─────────────────────────────────────────────


class TestDrainSwitchUnknownReportId(unittest.TestCase):
    def test_unknown_report_id_continues_drain(self):
        """raw[0] hors MSG_LENGTHS (>= 6 octets) → continue, pas de return."""
        kb = _make_keyboard()
        unknown_packet = bytes([0x99, 0xFF, 5, 0x00, 3, 1] + [0] * 6)  # 0x99 not in {0x10, 0x11, 0x12}
        valid_packet = bytes([0x11, 0xFF, 5, 0x00, 3, 2] + [0] * 14)
        kb.transport.read.side_effect = [unknown_packet, valid_packet] + [None] * 8
        result = _drain_switch(kb)
        self.assertEqual(result, 2)


# ── Tests _emit_arrival_switch ────────────────────────────────────────────────


class TestEmitArrivalSwitch(unittest.TestCase):
    def test_emits_when_long_enough(self):
        """Reconnexion longue + this_mac_host connu → SwitchEvent émis."""
        event_queue = queue.Queue()
        hunt_trigger = threading.Event()
        t, target = _emit_arrival_switch(
            reconnect_duration=2.0,
            this_mac_host=1,
            event_queue=event_queue,
            hunt_trigger=hunt_trigger,
            last_switch_time=0.0,
            last_switch_target=-1,
            name="MX Keys S",
        )
        self.assertFalse(event_queue.empty())
        event = event_queue.get_nowait()
        self.assertEqual(event.target_host, 1)
        self.assertTrue(hunt_trigger.is_set())
        self.assertEqual(target, 1)

    def test_no_emit_when_too_short(self):
        """Reconnexion courte (blip BT) → pas d'événement."""
        event_queue = queue.Queue()
        hunt_trigger = threading.Event()
        _emit_arrival_switch(
            reconnect_duration=0.1,
            this_mac_host=1,
            event_queue=event_queue,
            hunt_trigger=hunt_trigger,
            last_switch_time=0.0,
            last_switch_target=-1,
            name="MX Keys S",
        )
        self.assertTrue(event_queue.empty())

    def test_no_emit_when_host_is_none(self):
        """this_mac_host=None → pas d'événement."""
        event_queue = queue.Queue()
        hunt_trigger = threading.Event()
        _emit_arrival_switch(
            reconnect_duration=5.0,
            this_mac_host=None,
            event_queue=event_queue,
            hunt_trigger=hunt_trigger,
            last_switch_time=0.0,
            last_switch_target=-1,
            name="MX Keys S",
        )
        self.assertTrue(event_queue.empty())

    def test_debounce_same_target(self):
        """Même target récent → debounce → pas d'événement."""
        import time
        event_queue = queue.Queue()
        hunt_trigger = threading.Event()
        _emit_arrival_switch(
            reconnect_duration=5.0,
            this_mac_host=1,
            event_queue=event_queue,
            hunt_trigger=hunt_trigger,
            last_switch_time=time.time(),
            last_switch_target=1,
            name="MX Keys S",
        )
        self.assertTrue(event_queue.empty())


if __name__ == "__main__":
    unittest.main()
