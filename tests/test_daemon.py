"""Tests pour swigi.daemon — daemon dual-path orchestrateur."""

import contextlib
import queue
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# Mock hidapi_loader + gui AVANT import swigi
if "swigi.hidapi_loader" not in sys.modules:
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    _mock_loader.hid_err = MagicMock(return_value="mock error")
    sys.modules["swigi.hidapi_loader"] = _mock_loader

if "swigi.gui" not in sys.modules:
    _mock_gui = MagicMock()
    _mock_gui.notify = MagicMock()
    _mock_gui.prefs = {"mouse_follow": True}
    _mock_gui._prefs_lock = threading.Lock()
    _mock_gui.HAS_RUMPS = False
    _mock_gui.SwiGiMenuBar = None
    sys.modules["swigi.gui"] = _mock_gui

from swigi.daemon import (
    _apply_better_mouse,
    _mice_probe_loop,
    _reconnect_keyboard,
    _set_keyboard_status,
    run_daemon,
)
from swigi.transport import TransportError

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_device(
    name="MX Keys S", product_id=0xB35B, change_host_index=5, generation="push"
):
    device = MagicMock()
    device.name = name
    device.product_id = product_id
    device.change_host_index = change_host_index
    device.generation = generation
    device.transport = MagicMock()
    device.transport.is_open = True

    def _close():
        device.transport.is_open = False

    device.close.side_effect = _close
    return device


@contextlib.contextmanager
def _fast_timing():
    """Réduit les délais pour les tests."""
    patches = [
        patch("swigi.daemon._PROBE_INTERVAL", 0.05),
        patch("swigi.daemon._PROBE_FAST_INTERVAL", 0.02),
        patch("swigi.daemon._PROBE_FAST_DURATION", 0.2),
        patch("swigi.daemon._VERIFY_TIMEOUT", 2.0),
        patch("swigi.daemon._DISPATCHER_DEBOUNCE", 0.1),
        patch("swigi.daemon._STABILITY_WAIT", 0.0),
        patch("swigi.daemon._RECONNECT_DELAY", 0.01),
        patch("swigi.daemon._RECONNECT_MAX_DELAY", 0.02),
        patch("swigi.path_push._PING_INTERVAL", 0.0),
        patch("swigi.path_push._READ_WINDOW", 0.05),
        patch("swigi.path_push._DEBOUNCE", 0.1),
        patch("swigi.path_pull._PING_INTERVAL", 0.01),
    ]
    with contextlib.ExitStack() as stack:
        for timing_patch in patches:
            stack.enter_context(timing_patch)
        yield


# ── Tests _mice_probe_loop ────────────────────────────────────────────────────


class TestMiceProbeLoop(unittest.TestCase):
    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host")
    def test_confirms_correct_host(self, mock_get_host, mock_find):
        """Vérification post-switch : confirme quand souris sur bon hôte."""
        mouse = _make_device(name="MX Vertical", product_id=0xB034, change_host_index=9)
        found_mouse = _make_device(
            name="MX Vertical", product_id=0xB034, change_host_index=9
        )
        mock_get_host.return_value = 1

        mice = [mouse]
        state = {"last_target_host": 1, "last_switch_time": time.time()}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop(*args, **kwargs):
            stop_event.set()
            return [found_mouse]

        mock_find.side_effect = stop

        thread = threading.Thread(
            target=_mice_probe_loop,
            args=(mice, state, stop_event, hunt_trigger, mouse_lock),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=2.0)

        self.assertIsNone(state["last_target_host"])

    @_fast_timing()
    @patch("swigi.daemon._VERIFY_TIMEOUT", 10.0)
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host")
    @patch("swigi.daemon.send_change_host")
    def test_wrong_host_deferred_send(self, mock_send, mock_get_host, mock_find):
        """Souris sur mauvais hôte → envoi différé et clear target."""
        mouse = _make_device(name="MX Vertical", product_id=0xB034, change_host_index=9)
        # find_all_devices retourne un nouvel objet (pas le même que dans mice)
        found_mouse = _make_device(
            name="MX Vertical", product_id=0xB034, change_host_index=9
        )
        mock_get_host.return_value = 0

        mice = [mouse]
        state = {"last_target_host": 1, "last_switch_time": time.time() - 6.0}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop(*args, **kwargs):
            stop_event.set()
            return [found_mouse]

        mock_find.side_effect = stop

        thread = threading.Thread(
            target=_mice_probe_loop,
            args=(mice, state, stop_event, hunt_trigger, mouse_lock),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=2.0)

        mock_send.assert_called_once()
        self.assertIsNone(state.get("last_target_host"))

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host")
    def test_timeout_clears_target(self, mock_get_host, mock_find):
        """Après VERIFY_TIMEOUT, le target est abandonné."""
        mouse = _make_device(name="MX Vertical", product_id=0xB034, change_host_index=9)
        mock_get_host.return_value = 0
        mock_find.return_value = [mouse]

        mice = [mouse]
        # Switch il y a 11 secondes (> _VERIFY_TIMEOUT=2.0 en test)
        state = {"last_target_host": 1, "last_switch_time": time.time() - 11.0}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop(*args, **kwargs):
            stop_event.set()
            return [mouse]

        mock_find.side_effect = stop

        thread = threading.Thread(
            target=_mice_probe_loop,
            args=(mice, state, stop_event, hunt_trigger, mouse_lock),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=2.0)

        self.assertIsNone(state["last_target_host"])


# ── Tests run_daemon ──────────────────────────────────────────────────────────


@patch("swigi.path_push.get_current_host", return_value=0)
class TestRunDaemon(unittest.TestCase):
    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.send_change_host")
    def test_switch_sends_immediately(self, mock_send, mock_find, mock_get_host):
        """Switch → CHANGE_HOST envoyé immédiatement à la souris."""
        mock_find.return_value = []
        keyboard = _make_device(
            name="MX Keys S", product_id=0xB35B, change_host_index=5, generation="push"
        )
        mouse = _make_device(
            name="MX Vertical", product_id=0xB034, change_host_index=9
        )

        state = {}
        stop_event = threading.Event()

        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return packet
            time.sleep(0.1)
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        thread = threading.Thread(
            target=run_daemon,
            args=([keyboard], [mouse], state, stop_event),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=3.0)

        mock_send.assert_called()
        self.assertEqual(state.get("switches"), 1)

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.send_change_host")
    @patch("swigi.daemon.prefs", {"mouse_follow": False})
    def test_mouse_follow_disabled(self, mock_send, mock_find, mock_get_host):
        """Si mouse_follow=False, CHANGE_HOST n'est pas envoyé."""
        mock_find.return_value = []
        keyboard = _make_device(change_host_index=5)
        mouse = _make_device(change_host_index=9)

        state = {}
        stop_event = threading.Event()

        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 0] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] == 1:
                return packet
            time.sleep(0.1)
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        thread = threading.Thread(
            target=run_daemon,
            args=([keyboard], [mouse], state, stop_event),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=3.0)

        mock_send.assert_not_called()

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices", return_value=[])
    @patch("swigi.daemon.send_change_host")
    def test_pull_keyboard_reconnect_sends(self, mock_send, mock_find, mock_get_host):
        """Clavier PULL : déconnexion → reconnexion → PULL event → CHANGE_HOST envoyé."""
        keyboard = _make_device(
            name="MX Keys Wireless", product_id=0xB35B, change_host_index=5, generation="pull"
        )
        new_keyboard = _make_device(
            name="MX Keys Wireless", product_id=0xB35B, change_host_index=5, generation="pull"
        )
        mouse = _make_device(
            name="MX Vertical", product_id=0xB034, change_host_index=9
        )

        state = {}
        stop_event = threading.Event()

        # First ping fails → disconnect
        call_count = [0]

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TransportError("dead")

        keyboard.transport.write.side_effect = write_side_effect

        # After reconnect, stop quickly
        new_calls = [0]

        def new_write(data):
            new_calls[0] += 1
            if new_calls[0] >= 2:
                stop_event.set()

        new_keyboard.transport.write.side_effect = new_write

        with patch("swigi.path_pull.get_current_host", return_value=0), \
             patch("swigi.daemon.get_current_host", return_value=1), \
             patch("swigi.daemon._reconnect_keyboard", return_value=new_keyboard):
            thread = threading.Thread(
                target=run_daemon,
                args=([keyboard], [mouse], state, stop_event),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=4.0)

        mock_send.assert_called()
        self.assertEqual(state.get("switches"), 1)

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.send_change_host")
    def test_dispatcher_debounce_same_target(self, mock_send, mock_find, mock_get_host):
        """Debounce : même target < 1s → un seul dispatch."""
        mock_find.return_value = []
        keyboard = _make_device(change_host_index=5)
        mouse = _make_device(change_host_index=9)

        state = {}
        stop_event = threading.Event()

        # Deux notifications identiques rapprochées
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=10):
            calls[0] += 1
            if calls[0] <= 2:
                return packet
            time.sleep(0.1)
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read

        thread = threading.Thread(
            target=run_daemon,
            args=([keyboard], [mouse], state, stop_event),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=3.0)

        # Un seul dispatch malgré 2 notifications (debounce)
        self.assertEqual(state.get("switches"), 1)


# ── Tests _reconnect_keyboard ─────────────────────────────────────────────────


class TestReconnectKeyboard(unittest.TestCase):
    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    def test_finds_keyboard_by_product_id(self, mock_find):
        """Retrouve le clavier par PID et renvoie le DeviceInfo."""
        stop_event = threading.Event()
        keyboard = _make_device(product_id=0xB35B)
        mock_find.return_value = [keyboard]

        result = _reconnect_keyboard(0xB35B, stop_event)

        self.assertIs(result, keyboard)
        keyboard.transport.write.assert_called_once()

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    def test_returns_none_on_stop(self, mock_find):
        """Retourne None si stop_event est set."""
        stop_event = threading.Event()
        stop_event.set()
        mock_find.return_value = []

        result = _reconnect_keyboard(0xB35B, stop_event)
        self.assertIsNone(result)

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    def test_backoff_increases(self, mock_find):
        """Backoff exponentiel — appelle find_all_devices plusieurs fois."""
        stop_event = threading.Event()
        calls = [0]

        def side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] >= 3:
                stop_event.set()
            return []

        mock_find.side_effect = side_effect

        result = _reconnect_keyboard(0xB35B, stop_event)
        self.assertIsNone(result)
        self.assertGreaterEqual(calls[0], 2)

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    def test_ping_fail_closes_and_retries(self, mock_find):
        """Si ping échoue après trouvaille, close et retente."""
        stop_event = threading.Event()
        bad_keyboard = _make_device(product_id=0xB35B)
        bad_keyboard.transport.write.side_effect = TransportError("ping fail")
        good_keyboard = _make_device(product_id=0xB35B)
        calls = [0]

        def side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                return [bad_keyboard]
            if calls[0] == 2:
                return [good_keyboard]
            stop_event.set()
            return []

        mock_find.side_effect = side_effect

        result = _reconnect_keyboard(0xB35B, stop_event)
        self.assertIs(result, good_keyboard)
        bad_keyboard.close.assert_called_once()

    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    def test_wrong_pid_closed(self, mock_find):
        """Clavier avec mauvais PID est fermé."""
        stop_event = threading.Event()
        wrong_keyboard = _make_device(product_id=0x1111)
        right_keyboard = _make_device(product_id=0xB35B)
        calls = [0]

        def side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                return [wrong_keyboard]
            if calls[0] == 2:
                return [right_keyboard]
            stop_event.set()
            return []

        mock_find.side_effect = side_effect

        result = _reconnect_keyboard(0xB35B, stop_event)
        self.assertIs(result, right_keyboard)
        wrong_keyboard.close.assert_called_once()


# ── Tests _post_pull_event ────────────────────────────────────────────────────


class TestPostPullEvent(unittest.TestCase):
    @patch("swigi.daemon.get_current_host", return_value=1)
    def test_posts_event_with_current_host(self, mock_get_host):
        """Post un _SwitchEvent(this_mac_host, source='pull') après reconnexion."""
        from swigi.daemon import _post_pull_event, _SwitchEvent

        keyboard = _make_device()
        event_queue = queue.Queue()
        state = {}
        hunt_trigger = threading.Event()

        _post_pull_event(keyboard, event_queue, state, hunt_trigger, "MX Keys S")

        event = event_queue.get_nowait()
        self.assertIsInstance(event, _SwitchEvent)
        self.assertEqual(event.target_host, 1)
        self.assertEqual(event.source, "pull")
        self.assertEqual(state["this_mac_host"], 1)
        self.assertTrue(hunt_trigger.is_set())

    @patch("swigi.daemon.get_current_host", side_effect=TransportError("dead"))
    def test_falls_back_to_state(self, mock_get_host):
        """Fallback sur state['this_mac_host'] si get_current_host échoue."""
        from swigi.daemon import _post_pull_event

        keyboard = _make_device()
        event_queue = queue.Queue()
        state = {"this_mac_host": 2}
        hunt_trigger = threading.Event()

        _post_pull_event(keyboard, event_queue, state, hunt_trigger, "MX Keys S")

        event = event_queue.get_nowait()
        self.assertEqual(event.target_host, 2)
        self.assertEqual(event.source, "pull")

    @patch("swigi.daemon.get_current_host", return_value=None)
    def test_no_event_if_host_unknown(self, mock_get_host):
        """Pas d'event si get_current_host retourne None et pas de state."""
        from swigi.daemon import _post_pull_event

        keyboard = _make_device()
        event_queue = queue.Queue()
        state = {}
        hunt_trigger = threading.Event()

        _post_pull_event(keyboard, event_queue, state, hunt_trigger, "MX Keys S")

        self.assertTrue(event_queue.empty())
        self.assertFalse(hunt_trigger.is_set())


# ── Tests _set_keyboard_status ────────────────────────────────────────────────


class TestSetKeyboardStatus(unittest.TestCase):
    def test_updates_with_lock(self):
        """Met à jour state avec lock."""
        state = {"keyboards": {}, "_lock": threading.Lock()}
        _set_keyboard_status(state, 0xB35B, "MX Keys S", True)
        self.assertEqual(state["keyboards"][0xB35B], {"name": "MX Keys S", "ok": True})
        self.assertEqual(state["keyboard"], "MX Keys S")

    def test_updates_without_lock(self):
        """Met à jour state sans lock."""
        state = {"keyboards": {}}
        _set_keyboard_status(state, 0xB35B, "MX Keys S", False)
        self.assertEqual(state["keyboards"][0xB35B], {"name": "MX Keys S", "ok": False})
        self.assertIsNone(state["keyboard"])

    def test_keyboard_display_prefers_ok(self):
        """Le display montre le premier clavier ok=True."""
        state = {"keyboards": {}, "_lock": threading.Lock()}
        _set_keyboard_status(state, 0x1111, "Craft", False)
        _set_keyboard_status(state, 0x2222, "MX Keys", True)
        self.assertEqual(state["keyboard"], "MX Keys")


# ── Tests _apply_better_mouse ─────────────────────────────────────────────────


class TestApplyBetterMouse(unittest.TestCase):
    @patch("swigi.daemon.SYSTEM", "Darwin")
    @patch(
        "swigi.daemon.prefs",
        {"better_mouse_auto_apply": True, "better_mouse_profile": "gaming"},
    )
    @patch("swigi.bettermouse.apply_profile")
    def test_applies_profile(self, mock_apply):
        """Applique le profil si configuré."""
        _apply_better_mouse("MX Vertical")
        mock_apply.assert_called_once_with("gaming", mouse_name="MX Vertical")

    @patch("swigi.daemon.SYSTEM", "Linux")
    def test_skips_non_darwin(self):
        """Ne fait rien sur Linux."""
        _apply_better_mouse("MX Vertical")  # No error

    @patch("swigi.daemon.SYSTEM", "Darwin")
    @patch(
        "swigi.daemon.prefs",
        {"better_mouse_auto_apply": False, "better_mouse_profile": "gaming"},
    )
    def test_skips_if_disabled(self):
        """Ne fait rien si auto_apply=False."""
        _apply_better_mouse("MX Vertical")  # No error

    @patch("swigi.daemon.SYSTEM", "Darwin")
    @patch(
        "swigi.daemon.prefs",
        {"better_mouse_auto_apply": True, "better_mouse_profile": "gaming"},
    )
    @patch("swigi.bettermouse.apply_profile", side_effect=RuntimeError("BM crash"))
    def test_exception_logged_not_raised(self, mock_apply):
        """Exception dans apply_profile est loguée, pas propagée."""
        _apply_better_mouse("MX Vertical")  # Should not raise


if __name__ == "__main__":
    unittest.main()
