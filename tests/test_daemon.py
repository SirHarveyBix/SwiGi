"""Tests pour swigi.daemon — daemon simplifié."""

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
    _drain_switch,
    _mice_probe_loop,
    _reconnect_keyboard,
    _set_keyboard_status,
    _SwitchEvent,
    _watch_keyboard,
    run_daemon,
)
from swigi.transport import TransportError

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_device(name="MX Keys S", product_id=0xB35B, change_host_index=5):
    device = MagicMock()
    device.name = name
    device.product_id = product_id
    device.change_host_index = change_host_index
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
        patch("swigi.daemon._STABILITY_WAIT", 0.0),
        patch("swigi.daemon._RECONNECT_DELAY", 0.01),
        patch("swigi.daemon._RECONNECT_MAX_DELAY", 0.02),
        patch("swigi.daemon._PING_INTERVAL", 0.0),
        patch("swigi.daemon._READ_WINDOW", 0.05),
        patch("swigi.daemon._DEBOUNCE", 0.1),
        patch("swigi.daemon._VERIFY_TIMEOUT", 2.0),
    ]
    with contextlib.ExitStack() as stack:
        for timing_patch in patches:
            stack.enter_context(timing_patch)
        yield


# ── Tests _drain_switch ───────────────────────────────────────────────────────


class TestDrainSwitch(unittest.TestCase):
    def test_captures_switch_in_buffer(self):
        """Capture un paquet CHANGE_HOST dans le buffer de lecture."""
        keyboard = _make_device(change_host_index=5)
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        keyboard.transport.read.return_value = packet
        result = _drain_switch(keyboard)
        self.assertEqual(result, 1)

    def test_returns_none_if_no_switch(self):
        """Retourne None si aucun paquet CHANGE_HOST n'est trouvé."""
        keyboard = _make_device(change_host_index=5)
        keyboard.transport.read.return_value = None
        result = _drain_switch(keyboard)
        self.assertIsNone(result)

    def test_ignores_non_zero_swid(self):
        """Ignore les paquets avec swid non-zéro (réponses à nos requêtes)."""
        keyboard = _make_device(change_host_index=5)
        packet = bytes([0x11, 0xFF, 5, 0x0A, 3, 1] + [0] * 14)
        keyboard.transport.read.return_value = packet
        result = _drain_switch(keyboard)
        self.assertIsNone(result)

    def test_stops_on_transport_error(self):
        """Arrête la lecture sur TransportError et retourne None."""
        keyboard = _make_device(change_host_index=5)
        keyboard.transport.read.side_effect = TransportError("dead")
        result = _drain_switch(keyboard)
        self.assertIsNone(result)

    def test_invalid_target_ignored(self):
        """Ignore un target hors limites (target >= num_hosts)."""
        keyboard = _make_device(change_host_index=5)
        # target=5, num_hosts=3 → invalid
        packet = bytes([0x11, 0xFF, 5, 0x00, 3, 5] + [0] * 14)
        keyboard.transport.read.side_effect = [packet] + [None] * 10
        result = _drain_switch(keyboard)
        self.assertIsNone(result)


# ── Tests _watch_keyboard ─────────────────────────────────────────────────────


@patch("swigi.daemon.get_current_host", return_value=0)
class TestWatchKeyboard(unittest.TestCase):
    @_fast_timing()
    def test_switch_posted_immediately(self, mock_get_host):
        """Switch détecté → événement posté immédiatement (pas de commit wait)."""
        keyboard = _make_device(change_host_index=5)
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

        thread = threading.Thread(
            target=_watch_keyboard,
            args=(keyboard, event_queue, state, stop_event, hunt_trigger),
            daemon=True,
        )
        thread.start()
        event = event_queue.get(timeout=2.0)
        stop_event.set()
        thread.join(timeout=2.0)

        self.assertIsInstance(event, _SwitchEvent)
        self.assertEqual(event.target_host, 2)

    @_fast_timing()
    def test_debounce(self, mock_get_host):
        """Même switch dans la fenêtre de debounce → un seul événement."""
        keyboard = _make_device(change_host_index=5)
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

        thread = threading.Thread(
            target=_watch_keyboard,
            args=(keyboard, event_queue, state, stop_event, hunt_trigger),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=2.0)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        self.assertEqual(len(events), 1)

    @_fast_timing()
    @patch("swigi.daemon._reconnect_keyboard")
    def test_reconnect_on_write_failure(self, mock_reconnect, mock_get_host):
        """Échec write → reconnexion automatique."""
        keyboard = _make_device(change_host_index=5)
        new_keyboard = _make_device(change_host_index=5, name="MX Keys S (new)")
        new_keyboard.transport.read.return_value = None
        mock_reconnect.return_value = new_keyboard

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        calls = [0]
        keyboard.transport.write.side_effect = TransportError("dead")
        keyboard.transport.read.return_value = None

        def new_read(timeout=10):
            calls[0] += 1
            if calls[0] > 2:
                stop_event.set()
            return None

        new_keyboard.transport.read.side_effect = new_read

        thread = threading.Thread(
            target=_watch_keyboard,
            args=(keyboard, event_queue, state, stop_event, hunt_trigger),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=3.0)

        mock_reconnect.assert_called_once()


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


@patch("swigi.daemon.get_current_host", return_value=0)
class TestRunDaemon(unittest.TestCase):
    @_fast_timing()
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.send_change_host")
    def test_switch_sends_immediately(self, mock_send, mock_find, mock_get_host):
        """Switch → CHANGE_HOST envoyé immédiatement à la souris."""
        mock_find.return_value = []
        keyboard = _make_device(
            name="MX Keys S", product_id=0xB35B, change_host_index=5
        )
        mouse = _make_device(name="MX Vertical", product_id=0xB034, change_host_index=9)

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

        mock_send.assert_called_once()
        self.assertEqual(state.get("switches"), 1)
        self.assertEqual(state.get("last_target_host"), 1)

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


# ── Tests _watch_keyboard watchdog ────────────────────────────────────────────


class TestWatchKeyboardWatchdog(unittest.TestCase):
    @_fast_timing()
    @patch("swigi.daemon.get_current_host", return_value=0)
    @patch("swigi.daemon._reconnect_keyboard")
    def test_watchdog_triggers_reconnect(self, mock_reconnect, mock_get_host):
        """Watchdog → reconnexion après 10s sans réponse."""
        keyboard = _make_device(change_host_index=5)
        mock_reconnect.return_value = None  # reconnect fails → thread exits

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # Make write succeed but read never returns valid data
        keyboard.transport.write.return_value = None
        keyboard.transport.read.return_value = None

        # Patch time: first call sets last_response, all subsequent trigger watchdog
        base = 1000.0
        call_n = [0]

        def fake_time():
            call_n[0] += 1
            if call_n[0] == 1:
                return base  # last_response = time.time()
            return base + 15.0  # Immediately triggers watchdog (15 > 10)

        with patch("swigi.daemon.time.time", side_effect=fake_time):
            with patch("swigi.daemon.time.sleep"):
                thread = threading.Thread(
                    target=_watch_keyboard,
                    args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                    daemon=True,
                )
                thread.start()
                thread.join(timeout=3.0)

        mock_reconnect.assert_called_once()
        keyboard.close.assert_called()


if __name__ == "__main__":
    unittest.main()
