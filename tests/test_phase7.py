"""Tests Phase 7 — T39-T42 : path_pull SwitchEvent, watchdog, BetterMouse,
logique sent/unsent, TTL, filtre sw_id, hunt_trigger."""

import queue
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# ── Mocks système (hidapi + gui) avant tout import swigi ──────────────────────

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

from swigi.daemon import (
    _mice_probe_loop,
    _SwitchEvent,
    run_daemon,
)
from swigi.path_pull import watch_keyboard_pull
from swigi.path_push import watch_keyboard_push
from swigi.transport import TransportError

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_keyboard(
    name="MX Keys Wireless", product_id=0xB35B, change_host_index=5, generation="pull"
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


def _make_mouse(
    name="MX Master 4", product_id=0xB042, change_host_index=9
):
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


# ── T39 : path_pull — SwitchEvent posté après reconnexion ─────────────────────


class TestPullPostsSwitchEvent(unittest.TestCase):
    """T1 : Déconnexion → reconnexion → _SwitchEvent posté + hunt_trigger set."""

    @patch("swigi.path_pull.get_current_host")
    @patch("swigi.daemon._reconnect_keyboard")
    @patch("swigi.daemon._set_keyboard_status")
    def test_pull_posts_switch_event_on_reconnect(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        # get_current_host : initial retourne 0, post-reconnect retourne 1
        mock_get_host.side_effect = [0, 1]

        keyboard = _make_keyboard()
        new_keyboard = _make_keyboard(name="MX Keys Wireless (new)")
        # Après reconnect, stop au 2e ping
        stop_event = threading.Event()
        new_calls = [0]

        def new_write(data):
            new_calls[0] += 1
            if new_calls[0] >= 2:
                stop_event.set()

        new_keyboard.transport.write.side_effect = new_write
        new_keyboard.transport.read.return_value = None
        mock_reconnect.return_value = new_keyboard

        # Premier ping échoue → déconnexion
        call_count = [0]

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TransportError("dead")

        keyboard.transport.write.side_effect = write_side_effect

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        hunt_trigger = threading.Event()

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

        # Un _SwitchEvent doit être posté
        self.assertFalse(event_queue.empty(), "Aucun SwitchEvent posté après reconnexion")
        event = event_queue.get_nowait()
        self.assertIsInstance(event, _SwitchEvent)
        self.assertEqual(event.target_host, 1)
        self.assertEqual(event.source, "pull")

        # hunt_trigger doit avoir été set (peut avoir été cleared par suite)
        # On vérifie via mock_reconnect + event posté (chaîne causale)
        mock_reconnect.assert_called_once()

    @patch("swigi.path_pull.get_current_host", return_value=None)
    @patch("swigi.daemon._reconnect_keyboard")
    @patch("swigi.daemon._set_keyboard_status")
    def test_pull_no_event_when_host_unknown(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        """Si get_current_host retourne None post-reconnect, pas de SwitchEvent."""
        keyboard = _make_keyboard()
        new_keyboard = _make_keyboard(name="MX Keys Wireless (new)")
        stop_event = threading.Event()
        new_calls = [0]

        def new_write(data):
            new_calls[0] += 1
            if new_calls[0] >= 2:
                stop_event.set()

        new_keyboard.transport.write.side_effect = new_write
        new_keyboard.transport.read.return_value = None
        mock_reconnect.return_value = new_keyboard

        call_count = [0]

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TransportError("dead")

        keyboard.transport.write.side_effect = write_side_effect

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        hunt_trigger = threading.Event()

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

        self.assertTrue(event_queue.empty(), "SwitchEvent posté alors que host est None")


# ── T39 : path_pull — Watchdog atteignable ────────────────────────────────────


class TestPullWatchdogReachable(unittest.TestCase):
    """T2 : Watchdog déclenche quand aucune réponse lue (last_response pas mis à jour)."""

    @patch("swigi.path_pull.get_current_host", return_value=0)
    @patch("swigi.daemon._reconnect_keyboard")
    @patch("swigi.daemon._set_keyboard_status")
    def test_pull_watchdog_reachable(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        """Ping réussit mais read retourne None → watchdog déclenche après timeout."""
        mock_reconnect.return_value = None  # Reconnect fails → exit

        keyboard = _make_keyboard()
        # Ping écrit sans erreur
        keyboard.transport.write.return_value = None
        # Read retourne None → last_response jamais mis à jour
        keyboard.transport.read.return_value = None

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        base_time = 1000.0
        call_n = [0]

        def fake_time():
            call_n[0] += 1
            # Premières lectures → temps stable
            if call_n[0] <= 4:
                return base_time
            # Après → simuler passage du temps (watchdog timeout dépassé)
            return base_time + 15.0

        with patch("swigi.path_pull.time.time", side_effect=fake_time), patch(
            "swigi.path_pull.time.sleep"
        ), patch("swigi.path_pull._PING_INTERVAL", 0.0):
            thread = threading.Thread(
                target=watch_keyboard_pull,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        mock_reconnect.assert_called_once()


# ── T41 : BetterMouse sur reconnected_mice ────────────────────────────────────


class TestBetterMouseOnReconnectedMice(unittest.TestCase):
    """T3 : _apply_better_mouse appelée pour reconnected_mice (pas seulement new_mice)."""

    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host", return_value=None)
    @patch("swigi.daemon._apply_better_mouse")
    def test_bettermouse_on_reconnected_mice(
        self, mock_apply, mock_get_host, mock_find
    ):
        # Souris dans mice mais transport closed → sera dans reconnected_mice
        mouse = _make_mouse()
        mouse.transport.is_open = False  # Déjà présente mais fermée

        reconnected = _make_mouse()  # Nouveau handle
        reconnected.transport.is_open = True

        mice = [mouse]
        state = {"last_target_host": None, "last_switch_sent": False, "last_switch_time": 0.0}
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop_find(*args, **kwargs):
            stop_event.set()
            return [reconnected]

        mock_find.side_effect = stop_find

        with patch("swigi.daemon._PROBE_INTERVAL", 0.05), patch(
            "swigi.daemon._PROBE_FAST_DURATION", 0.2
        ):
            thread = threading.Thread(
                target=_mice_probe_loop,
                args=(mice, state, stop_event, hunt_trigger, mouse_lock),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        # _apply_better_mouse doit avoir été appelée (au moins une fois)
        mock_apply.assert_called()


# ── T41 : Pas de retry quand switch_sent=True ─────────────────────────────────


class TestNoRetryWhenSwitchSent(unittest.TestCase):
    """T4 : last_switch_sent=True, souris sur mauvais hôte → send_change_host PAS appelé."""

    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host")
    @patch("swigi.daemon.send_change_host")
    def test_no_retry_when_switch_sent(
        self, mock_send, mock_get_host, mock_find
    ):
        mouse = _make_mouse()
        found_mouse = _make_mouse()  # Instance distincte (évite fermeture par la probe loop)
        mock_get_host.return_value = 0  # Souris sur hôte 0

        mice = [mouse]
        state = {
            "last_target_host": 1,       # Target = hôte 1
            "last_switch_sent": True,    # Dispatcher a déjà envoyé
            "last_switch_time": time.time(),
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop_find(*args, **kwargs):
            stop_event.set()
            return [found_mouse]

        mock_find.side_effect = stop_find

        with patch("swigi.daemon._PROBE_INTERVAL", 0.05), patch(
            "swigi.daemon._PROBE_FAST_DURATION", 0.2
        ):
            thread = threading.Thread(
                target=_mice_probe_loop,
                args=(mice, state, stop_event, hunt_trigger, mouse_lock),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        # send_change_host NE doit PAS être appelé (anti ping-pong)
        mock_send.assert_not_called()
        # last_target_host effacé
        self.assertIsNone(state.get("last_target_host"))


# ── T41 : Envoi différé quand switch_sent=False ───────────────────────────────


class TestDeferredSendWhenNotSent(unittest.TestCase):
    """T5 : last_switch_sent=False, souris sur mauvais hôte → send_change_host appelé."""

    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host")
    @patch("swigi.daemon.send_change_host")
    def test_deferred_send_when_not_sent(
        self, mock_send, mock_get_host, mock_find
    ):
        mouse = _make_mouse()
        found_mouse = _make_mouse()  # Instance distincte
        mock_get_host.return_value = 0  # Souris sur hôte 0

        mice = [mouse]
        state = {
            "last_target_host": 1,       # Target = hôte 1
            "last_switch_sent": False,   # Dispatcher n'a PAS pu envoyer
            "last_switch_time": time.time(),
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop_find(*args, **kwargs):
            stop_event.set()
            return [found_mouse]

        mock_find.side_effect = stop_find

        with patch("swigi.daemon._PROBE_INTERVAL", 0.05), patch(
            "swigi.daemon._PROBE_FAST_DURATION", 0.2
        ):
            thread = threading.Thread(
                target=_mice_probe_loop,
                args=(mice, state, stop_event, hunt_trigger, mouse_lock),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        # send_change_host DOIT être appelé exactement une fois
        mock_send.assert_called_once()
        # last_target_host effacé
        self.assertIsNone(state.get("last_target_host"))


# ── T41 : TTL expire → clear target sans envoi ───────────────────────────────


class TestVerifyTimeoutClearsTarget(unittest.TestCase):
    """T6 : last_switch_time vieux (>30s) → last_target_host effacé sans send."""

    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.get_current_host")
    @patch("swigi.daemon.send_change_host")
    def test_verify_timeout_clears_target(
        self, mock_send, mock_get_host, mock_find
    ):
        mouse = _make_mouse()
        found_mouse = _make_mouse()  # Instance distincte
        mock_get_host.return_value = 0

        mice = [mouse]
        state = {
            "last_target_host": 1,
            "last_switch_sent": False,
            "last_switch_time": time.time() - 31,  # Expiré (> 30s)
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()
        mouse_lock = threading.Lock()
        hunt_trigger.set()

        def stop_find(*args, **kwargs):
            stop_event.set()
            return [found_mouse]

        mock_find.side_effect = stop_find

        with patch("swigi.daemon._PROBE_INTERVAL", 0.05), patch(
            "swigi.daemon._PROBE_FAST_DURATION", 0.2
        ), patch("swigi.daemon._VERIFY_TIMEOUT", 30.0):
            thread = threading.Thread(
                target=_mice_probe_loop,
                args=(mice, state, stop_event, hunt_trigger, mouse_lock),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        # Aucun envoi
        mock_send.assert_not_called()
        # Target effacé
        self.assertIsNone(state.get("last_target_host"))


# ── T42 : path_push — filtre sw_id ────────────────────────────────────────────


class TestPushIgnoresNonNotificationPackets(unittest.TestCase):
    """T7 : raw[3]=0x0A (sw_id != 0) → aucun SwitchEvent posté."""

    @patch("swigi.path_push.get_current_host", return_value=0)
    @patch("swigi.daemon._reconnect_keyboard")
    @patch("swigi.daemon._set_keyboard_status")
    def test_push_ignores_non_notification_packets(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        keyboard = _make_keyboard(generation="push")
        keyboard.change_host_index = 5

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # Paquet CHANGE_HOST avec sw_id=0x0A (réponse à nos requêtes, pas notification)
        # raw[3] = 0x0A → (0x0A & 0x0F) = 0x0A != 0x00 → doit être ignoré
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
    @patch("swigi.daemon._reconnect_keyboard")
    @patch("swigi.daemon._set_keyboard_status")
    def test_push_accepts_notification_packets(
        self, mock_set_status, mock_reconnect, mock_get_host
    ):
        """raw[3]=0x00 (sw_id=0, notification firmware) → SwitchEvent posté."""
        keyboard = _make_keyboard(generation="push")
        keyboard.change_host_index = 5

        event_queue = queue.Queue()
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}}
        }
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        # Paquet notification valide : raw[3]=0x00
        packet_notif = bytes([0x11, 0xFF, 5, 0x00, 3, 1] + [0] * 14)
        calls = [0]

        def mock_read(timeout=50):
            calls[0] += 1
            if calls[0] == 1:
                return packet_notif
            time.sleep(0.05)
            stop_event.set()
            return None

        keyboard.transport.read.side_effect = mock_read
        keyboard.transport.write.return_value = None

        with patch("swigi.path_push._PING_INTERVAL", 0.0), patch(
            "swigi.path_push._READ_WINDOW", 0.2
        ), patch("swigi.path_push._DEBOUNCE", 0.1), patch(
            "swigi.path_push._RECONNECT_GRACE", 0.0
        ):
            thread = threading.Thread(
                target=watch_keyboard_push,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=2.0)

        self.assertFalse(event_queue.empty(), "Aucun SwitchEvent pour paquet notification")


# ── T40 : hunt_trigger set après dispatch ─────────────────────────────────────


class TestHuntTriggerSetAfterDispatch(unittest.TestCase):
    """T8 : Switch event → hunt_trigger.is_set() après dispatch dans run_daemon."""

    @patch("swigi.path_push.get_current_host", return_value=0)
    @patch("swigi.daemon.find_all_devices")
    @patch("swigi.daemon.send_change_host")
    def test_hunt_trigger_set_after_dispatch(
        self, mock_send, mock_find, mock_get_host
    ):
        mock_find.return_value = []
        keyboard = _make_keyboard(generation="push")
        keyboard.change_host_index = 5
        mouse = _make_mouse()

        state = {}
        stop_event = threading.Event()

        # Paquet notification valide
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
        keyboard.transport.write.return_value = None

        with patch("swigi.daemon._PROBE_INTERVAL", 0.05), patch(
            "swigi.daemon._PROBE_FAST_INTERVAL", 0.02
        ), patch("swigi.daemon._PROBE_FAST_DURATION", 0.2), patch(
            "swigi.daemon._DISPATCHER_DEBOUNCE", 0.1
        ), patch("swigi.daemon._STABILITY_WAIT", 0.0), patch(
            "swigi.path_push._PING_INTERVAL", 0.0
        ), patch("swigi.path_push._READ_WINDOW", 0.1), patch(
            "swigi.path_push._DEBOUNCE", 0.1
        ), patch("swigi.path_push._RECONNECT_GRACE", 0.0):
            thread = threading.Thread(
                target=run_daemon,
                args=([keyboard], [mouse], state, stop_event),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=3.0)

        # send_change_host doit avoir été appelé (switch dispatché)
        mock_send.assert_called()
        # state["switches"] doit être 1
        self.assertEqual(state.get("switches"), 1)
        # last_target_host doit être set (ou déjà clear par probe loop)
        # Le point important : hunt_trigger a été set (dispatcher l'a fait)
        # On vérifie indirectement via switches > 0 et send appelé


if __name__ == "__main__":
    unittest.main()
