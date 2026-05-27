import contextlib
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# Mock hidapi_loader + gui AVANT tout import swigi (effets de bord)
if "swigi.hidapi_loader" in sys.modules:
    _mock_loader = sys.modules["swigi.hidapi_loader"]
else:
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    _mock_loader.hid_err = MagicMock(return_value="mock error")
    _mock_loader.DeviceInfoStruct = MagicMock()
    sys.modules["swigi.hidapi_loader"] = _mock_loader

if "swigi.gui" in sys.modules:
    _mock_gui = sys.modules["swigi.gui"]
else:
    _mock_gui = MagicMock()
    _mock_gui.notify = MagicMock()
    _mock_gui.prefs = {}
    _mock_gui.HAS_RUMPS = False
    _mock_gui.SwiGiMenuBar = None
    sys.modules["swigi.gui"] = _mock_gui

from swigi.daemon import (  # noqa: E402
    _apply_better_mouse_profile_if_needed,
    _check_and_apply_pending_host,
    _resync_pending_host_from_keyboard,
    _find_keyboard_by_product_id,
    _mice_probe_loop,
    _send_to_all_mice,
    _watch_keyboard,
)
from swigi.transport import TransportError  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_mouse(change_host_index=9, name="MX Vertical"):
    mouse = MagicMock()
    mouse.name = name
    mouse.change_host_index = change_host_index
    mouse.transport.is_open = True
    return mouse


def _make_keyboard(change_host_index=5, name="MX Keys S"):
    keyboard = MagicMock()
    keyboard.name = name
    keyboard.change_host_index = change_host_index
    keyboard.transport.is_open = True
    return keyboard


def _pending(host, ttl=60.0):
    return (host, time.time() + ttl)


@contextlib.contextmanager
def _fast_probe():
    """Réduit tous les délais de 50x pour éviter thread explosion en tests."""
    patches = [
        patch("swigi.daemon._MOUSE_HUNT_INTERVAL", 0.02),
        patch("swigi.daemon._MOUSE_PROBE_INTERVAL", 0.1),
        patch("swigi.daemon._MOUSE_HUNT_WINDOW", 0.5),
        patch("swigi.daemon._KEYBOARD_STABILITY_SECONDS", 0.0),
        patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        patch("swigi.daemon._KEYBOARD_RECONNECT_INITIAL_DELAY", 0.02),
        patch("swigi.daemon._KEYBOARD_RECONNECT_MAX_DELAY", 0.1),
        patch("swigi.daemon._KEYBOARD_READ_WINDOW", 0.12),
        patch("swigi.daemon._DRAIN_MAX_READS", 24),
        patch("swigi.daemon._DRAIN_NONE_STREAK", 3),
        patch("swigi.daemon._DRAIN_ERR_STREAK", 10),
        patch("swigi.daemon._KEYBOARD_PING_INTERVAL", 0.0),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


# ── _check_and_apply_pending_host ──────────────────────────────────────────────


class TestCheckAndApplyPendingHost(unittest.TestCase):
    def test_no_pending_returns_false(self):
        mouse = _make_mouse()
        state = {"pending_host": None, "mouse": "MX Vertical"}
        self.assertFalse(_check_and_apply_pending_host(mouse, state))
        mouse.close.assert_not_called()

    def test_expired_ttl_clears_and_returns_false(self):
        mouse = _make_mouse()
        state = {"pending_host": (1, time.time() - 1.0), "mouse": "MX Vertical"}
        self.assertFalse(_check_and_apply_pending_host(mouse, state))
        self.assertIsNone(state["pending_host"])
        mouse.close.assert_not_called()

    def test_sync_ok_clears_pending_returns_false(self):
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=1):
            self.assertFalse(_check_and_apply_pending_host(mouse, state))
        self.assertIsNone(state["pending_host"])
        mouse.close.assert_not_called()

    def test_get_current_host_none_keeps_pending(self):
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=None):
            self.assertFalse(_check_and_apply_pending_host(mouse, state))
        self.assertIsNotNone(state["pending_host"])
        mouse.close.assert_not_called()

    def test_desync_sends_correction_closes_mouse(self):
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical", "last_change_host_at": time.time()}
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        mock_send.assert_called_once()
        mouse.close.assert_called_once()
        self.assertIsNone(state["mouse"])
        self.assertIsNone(state["pending_host"])

    def test_desync_correction_fails_keeps_pending_closes_mouse(self):
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical", "last_change_host_at": time.time()}
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host", side_effect=TransportError("dead")),
        ):
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        mouse.close.assert_called_once()
        self.assertIsNone(state["mouse"])
        self.assertIsNotNone(state["pending_host"])

    # ── Scénarios 3 hôtes ──────────────────────────────────────────────────────

    def test_3hosts_sync_on_host2_ok(self):
        """Switch vers hôte 2 (3e Mac) — sync confirmée, pas de correction."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(2), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=2):
            self.assertFalse(_check_and_apply_pending_host(mouse, state))
        self.assertIsNone(state["pending_host"])
        mouse.close.assert_not_called()

    def test_3hosts_desync_host1_expected_host2(self):
        """Souris sur hôte 1 mais attendue sur hôte 2 — correction envoyée."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(2), "mouse": "MX Vertical", "last_change_host_at": time.time()}
        with (
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 2)

    def test_3hosts_desync_host2_expected_host0(self):
        """Retour hôte 2→0 avec souris bloquée sur hôte 2 — correction vers 0."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(0), "mouse": "MX Vertical", "last_change_host_at": time.time()}
        with (
            patch("swigi.daemon.get_current_host", return_value=2),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 0)

    def test_resync_mismatch_after_grace_keeps_manual_position(self):
        """Pending issu d'un resync ancien + désync → ne pas forcer, garder la position manuelle."""
        mouse = _make_mouse()
        state = {
            "pending_host": _pending(1),
            "pending_source": "resync",
            "mouse": "MX Vertical",
            "last_change_host_at": 0.0,
        }

        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        self.assertIsNone(state["pending_host"])
        self.assertGreater(state.get("manual_override_until", 0.0), time.time())

    def test_manual_override_active_skips_correction(self):
        """Pendant le cooldown manuel, aucune correction ne doit être envoyée."""
        mouse = _make_mouse()
        state = {
            "pending_host": _pending(1),
            "mouse": "MX Vertical",
            "manual_override_until": time.time() + 5.0,
            "last_change_host_at": time.time(),
        }

        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        self.assertIsNone(state["pending_host"])


# ── _resync_pending_host_from_keyboard ─────────────────────────────────────────


class TestResyncPendingHostFromKeyboard(unittest.TestCase):
    """Tests du fix principal : resync pending_host après reconnexion clavier."""

    def test_sets_pending_host_to_keyboard_current_host(self):
        keyboard = _make_keyboard()
        state = {"pending_host": None}
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)
        self.assertIsNotNone(state["pending_host"])
        self.assertEqual(state["pending_host"][0], 0)

    def test_preserves_pending_host_when_kb_unreadable(self):
        """Bug 4 fix : si le clavier est illisible après tous les retries,
        un pending_host existant (non-None) est conservé plutôt qu'effacé.
        Un pending stale est préférable à perdre la cible du switch."""
        keyboard = _make_keyboard()
        existing = _pending(1)
        state = {"pending_host": existing}
        with patch("swigi.daemon.get_current_host", return_value=None):
            _resync_pending_host_from_keyboard(keyboard, state)
        # pending_host doit être conservé (même objet, host=1)
        self.assertIsNotNone(state["pending_host"])
        self.assertEqual(state["pending_host"][0], 1)

    def test_clears_pending_host_when_kb_unreadable_and_none(self):
        """Si pending_host était None avant resync et le clavier est illisible,
        pending_host reste None (pas de régression)."""
        keyboard = _make_keyboard()
        state = {"pending_host": None}
        with patch("swigi.daemon.get_current_host", return_value=None):
            _resync_pending_host_from_keyboard(keyboard, state)
        self.assertIsNone(state["pending_host"])

    def test_overwrites_stale_pending_host(self):
        """Bug principal : pending_host stale (hôte 1) remplacé par hôte réel (0)."""
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(1)}  # stale depuis switch précédent
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)
        self.assertEqual(state["pending_host"][0], 0)

    def test_3hosts_stale_host2_kb_returns_to_host0(self):
        """3 hôtes : pending stale=2, clavier revient sur 0 — recalé à 0."""
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(2)}
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)
        self.assertEqual(state["pending_host"][0], 0)

    def test_3hosts_kb_reconnects_on_host1(self):
        """Clavier revient sur hôte 1 (2e Mac) — pending_host pointe vers 1."""
        keyboard = _make_keyboard()
        state = {"pending_host": None}
        with patch("swigi.daemon.get_current_host", return_value=1):
            _resync_pending_host_from_keyboard(keyboard, state)
        self.assertEqual(state["pending_host"][0], 1)

    def test_pending_ttl_is_refreshed(self):
        """TTL pending_host recalculé à la reconnexion clavier."""
        keyboard = _make_keyboard()
        old_deadline = time.time() + 1.0  # expire dans 1s
        state = {"pending_host": (0, old_deadline)}
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)
        new_deadline = state["pending_host"][1]
        self.assertGreater(new_deadline, old_deadline)

    def test_second_keyboard_same_host(self):
        """2e clavier avec change_host_index différent — resync avec son propre idx."""
        keyboard2 = _make_keyboard(change_host_index=7, name="MX Keys S 2")
        state = {"pending_host": _pending(2)}
        with patch("swigi.daemon.get_current_host", return_value=0) as mock_gch:
            _resync_pending_host_from_keyboard(keyboard2, state)
        # Vérifie que get_current_host est appelé avec le bon change_host_index
        _, _, feat_idx = mock_gch.call_args[0]
        self.assertEqual(feat_idx, 7)
        self.assertEqual(state["pending_host"][0], 0)


# ── Scénario intégration : round-trip A→B→A ────────────────────────────────────


class TestRoundTripScenario(unittest.TestCase):
    """Scénario complet : switch Mac→PC→Mac avec vérification souris."""

    def test_return_switch_no_spurious_correction(self):
        """
        Bug corrigé : après A→B→A, la souris ne doit PAS être renvoyée sur B.

        Avant fix : pending_host=(B, deadline) stale → correction vers B au retour.
        Après fix  : resync depuis clavier → pending_host=(A, deadline) → sync OK.
        """
        mouse = _make_mouse()
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}  # stale de A→B

        # Clavier revient sur hôte 0 (Mac = A)
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)

        # pending_host recalé sur 0
        self.assertEqual(state["pending_host"][0], 0)

        # Souris aussi revenue sur hôte 0 — pas de correction
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        mouse.close.assert_not_called()

    def test_3hosts_cycle_no_spurious_correction(self):
        """
        3 hôtes : A(0)→B(1)→C(2)→A(0) — souris ne doit pas bouger au retour sur A.
        """
        mouse = _make_mouse()
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(2), "mouse": "MX Vertical"}  # stale de B→C

        # Clavier revient sur hôte 0 (A)
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertEqual(state["pending_host"][0], 0)

        # Souris aussi sur 0 — pas de correction
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()

    def test_desync_still_corrected_after_resync(self):
        """
        Après resync, vraie désync (souris sur mauvais hôte) → correction appliquée.
        """
        mouse = _make_mouse()
        keyboard = _make_keyboard()
        # last_change_host_at récent : simule un switch SwiGi (grace period active)
        state = {"pending_host": _pending(1), "mouse": "MX Vertical", "last_change_host_at": time.time()}

        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)  # pending_host → 0

        # Souris sur hôte 1 (désync réelle) → correction vers 0
        with (
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 0)

    def test_two_keyboards_independent_resync(self):
        """2 claviers : chacun resync pending_host via son propre change_host_index."""
        state = {"pending_host": _pending(2)}

        keyboard1 = _make_keyboard(change_host_index=5, name="MX Keys S")
        keyboard2 = _make_keyboard(change_host_index=7, name="MX Keys Mini")

        captured_idx = []

        def fake_gch(transport, devnum, feat_idx, **kwargs):
            captured_idx.append(feat_idx)
            return 0

        with patch("swigi.daemon.get_current_host", side_effect=fake_gch):
            _resync_pending_host_from_keyboard(keyboard1, state)
            _resync_pending_host_from_keyboard(keyboard2, state)

        self.assertEqual(captured_idx, [5, 7])
        self.assertEqual(state["pending_host"][0], 0)

    def test_two_mice_pending_host_applies_to_active_mouse(self):
        """2 souris présentes : _check_and_apply_pending_host corrige la souris active."""
        mouse1 = _make_mouse(change_host_index=9, name="MX Vertical")
        mouse2 = _make_mouse(change_host_index=11, name="MX Master 4")
        state = {"pending_host": _pending(1), "mouse": "MX Vertical", "last_change_host_at": time.time()}

        # mouse1 sur hôte 0, attendue sur 1 → correction
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            _check_and_apply_pending_host(mouse1, state)

        _, _, feat_idx, target = mock_send.call_args[0]
        self.assertEqual(feat_idx, 9)  # change_host_index de mouse1
        self.assertEqual(target, 1)

        # mouse2 non touchée
        mock_send.reset_mock()
        state2 = {"pending_host": _pending(1), "mouse": "MX Master 4", "last_change_host_at": time.time()}
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send2,
        ):
            _check_and_apply_pending_host(mouse2, state2)

        _, _, feat_idx2, _ = mock_send2.call_args[0]
        self.assertEqual(feat_idx2, 11)  # change_host_index de mouse2


# ── _apply_better_mouse_profile_if_needed ────────────────────────────────────────────────


class TestApplyBetterMouseProfileIfNeeded(unittest.TestCase):
    def test_no_op_when_bm_auto_apply_false(self):
        _mock_gui.prefs = {
            "better_mouse_auto_apply": False,
            "better_mouse_profile": "mon-profil",
        }
        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.SYSTEM", "Darwin"),
        ):
            with patch("swigi.bettermouse.apply_profile") as mock_ap:
                _apply_better_mouse_profile_if_needed("MX Vertical")
        mock_ap.assert_not_called()

    def test_no_op_when_bm_profile_missing(self):
        _mock_gui.prefs = {"better_mouse_auto_apply": True}
        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.SYSTEM", "Darwin"),
        ):
            with patch("swigi.bettermouse.apply_profile") as mock_ap:
                _apply_better_mouse_profile_if_needed("MX Vertical")
        mock_ap.assert_not_called()

    def test_no_op_on_non_darwin(self):
        _mock_gui.prefs = {
            "better_mouse_auto_apply": True,
            "better_mouse_profile": "mon-profil",
        }
        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.SYSTEM", "Windows"),
        ):
            with patch("swigi.bettermouse.apply_profile") as mock_ap:
                _apply_better_mouse_profile_if_needed("MX Vertical")
        mock_ap.assert_not_called()

    def test_calls_apply_profile_when_configured(self):
        _mock_gui.prefs = {
            "better_mouse_auto_apply": True,
            "better_mouse_profile": "mon-profil",
        }
        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.SYSTEM", "Darwin"),
            patch("swigi.daemon.notify"),
            patch("swigi.bettermouse.apply_profile") as mock_ap,
        ):
            _apply_better_mouse_profile_if_needed("MX Vertical")
        mock_ap.assert_called_once_with("mon-profil", mouse_name="MX Vertical")

    def test_swallows_value_error_profile_mismatch(self):
        _mock_gui.prefs = {
            "better_mouse_auto_apply": True,
            "better_mouse_profile": "profil-mx",
        }
        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.SYSTEM", "Darwin"),
            patch("swigi.daemon.notify"),
            patch(
                "swigi.bettermouse.apply_profile",
                side_effect=ValueError("mauvaise souris"),
            ),
        ):
            _apply_better_mouse_profile_if_needed("MX Anywhere")  # ne doit pas lever

    def test_swallows_unexpected_exception(self):
        _mock_gui.prefs = {
            "better_mouse_auto_apply": True,
            "better_mouse_profile": "profil-mx",
        }
        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.SYSTEM", "Darwin"),
            patch("swigi.daemon.notify"),
            patch("swigi.bettermouse.apply_profile", side_effect=RuntimeError("boom")),
        ):
            _apply_better_mouse_profile_if_needed("MX Vertical")  # ne doit pas lever


# ── _find_keyboard_by_product_id ────────────────────────────────────────────────────────────


class TestFindKeyboardByProductId(unittest.TestCase):
    def test_returns_kb_with_matching_pid(self):
        """find_kb_by_pid retourne le clavier dont le PID correspond."""
        keyboard1 = _make_keyboard(name="MX Keys S")
        keyboard1.product_id = 0xB35B
        keyboard2 = _make_keyboard(name="MX Keys Mini")
        keyboard2.product_id = 0xB361

        with patch(
            "swigi.daemon.find_all_devices", return_value=[keyboard1, keyboard2]
        ):
            result = _find_keyboard_by_product_id(0xB35B)

        self.assertIsNotNone(result)
        self.assertEqual(result.product_id, 0xB35B)
        self.assertEqual(result.name, "MX Keys S")

    def test_closes_non_matching_candidates(self):
        """Les claviers dont le PID ne correspond pas doivent être fermés."""
        keyboard1 = _make_keyboard(name="MX Keys S")
        keyboard1.product_id = 0xB35B
        keyboard2 = _make_keyboard(name="MX Keys Mini")
        keyboard2.product_id = 0xB361

        with patch(
            "swigi.daemon.find_all_devices", return_value=[keyboard1, keyboard2]
        ):
            _find_keyboard_by_product_id(0xB35B)

        # keyboard2 ne correspond pas, doit être fermé
        keyboard2.close.assert_called_once()
        # keyboard1 correspond, ne doit pas être fermé par find_kb_by_pid
        keyboard1.close.assert_not_called()

    def test_returns_none_when_pid_not_found(self):
        """Retourne None si aucun clavier avec ce PID n'est disponible."""
        keyboard1 = _make_keyboard(name="MX Keys S")
        keyboard1.product_id = 0xB35B

        with patch("swigi.daemon.find_all_devices", return_value=[keyboard1]):
            result = _find_keyboard_by_product_id(0xDEAD)

        self.assertIsNone(result)
        keyboard1.close.assert_called_once()

    def test_returns_none_when_no_keyboards(self):
        """Retourne None si find_all_devices retourne une liste vide."""
        with patch("swigi.daemon.find_all_devices", return_value=[]):
            result = _find_keyboard_by_product_id(0xB35B)

        self.assertIsNone(result)


# ── _send_to_all_mice ──────────────────────────────────────────────────────────


class TestSendToAllMice(unittest.TestCase):
    def _make_lock(self):
        import threading

        return threading.Lock()

    def test_sends_to_single_mouse(self):
        """Avec une seule souris, envoie CHANGE_HOST et ferme le transport."""
        mouse = _make_mouse(change_host_index=9, name="MX Vertical")
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice, 1, state, lock)

        mock_send.assert_called_once()
        mouse.close.assert_called_once()
        self.assertIsNone(state["mouse"])
        self.assertEqual(state["mice"], [])

    def test_sends_to_all_mice_when_multiple(self):
        """Avec 2 souris, envoie CHANGE_HOST aux deux et ferme les deux transports."""
        mouse1 = _make_mouse(change_host_index=9, name="MX Vertical")
        mouse2 = _make_mouse(change_host_index=11, name="MX Master 4")
        mice = [mouse1, mouse2]
        state = {
            "pending_host": None,
            "mouse": "MX Vertical",
            "mice": ["MX Vertical", "MX Master 4"],
        }
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice, 2, state, lock)

        self.assertEqual(mock_send.call_count, 2)
        mouse1.close.assert_called_once()
        mouse2.close.assert_called_once()

    def test_skips_mouse_with_closed_transport(self):
        """Souris avec transport fermé : skippée, pas d'envoi CHANGE_HOST."""
        mouse_open = _make_mouse(change_host_index=9, name="MX Vertical")
        mouse_closed = _make_mouse(change_host_index=11, name="MX Master 4")
        mouse_closed.transport.is_open = False

        mice = [mouse_open, mouse_closed]
        state = {
            "pending_host": None,
            "mouse": "MX Vertical",
            "mice": ["MX Vertical", "MX Master 4"],
        }
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice, 1, state, lock)

        # Seule la souris avec transport ouvert reçoit la commande
        self.assertEqual(mock_send.call_count, 1)
        # La souris fermée ne reçoit pas de close supplémentaire
        mouse_closed.close.assert_not_called()

    def test_updates_pending_host_in_state(self):
        """Met à jour state["pending_host"] avec le bon hôte cible."""
        mouse = _make_mouse()
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host"):
            _send_to_all_mice(mice, 2, state, lock)

        self.assertIsNotNone(state["pending_host"])
        self.assertEqual(state["pending_host"][0], 2)

    def test_send_failure_closes_mouse(self):
        """Si send_change_host échoue, la souris est quand même fermée."""
        mouse = _make_mouse()
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host", side_effect=TransportError("dead")):
            _send_to_all_mice(mice, 1, state, lock)

        mouse.close.assert_called_once()


# ── Test 2 claviers → 2 events dans la queue ──────────────────────────────────


class TestTwoKeyboardsEvents(unittest.TestCase):
    def test_two_keyboards_fire_independent_events(self):
        """2 claviers qui switchent indépendamment → 2 _SwitchEvent dans la queue."""
        import queue as q_module
        import threading
        from swigi.daemon import _SwitchEvent

        event_queue = q_module.Queue()
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        keyboard1 = _make_keyboard(change_host_index=5, name="MX Keys S")
        keyboard1.product_id = 0xB35B
        keyboard2 = _make_keyboard(change_host_index=7, name="MX Keys Mini")
        keyboard2.product_id = 0xB361

        state = {
            "keyboards": {
                keyboard1.product_id: {"name": keyboard1.name, "ok": True},
                keyboard2.product_id: {"name": keyboard2.name, "ok": True},
            },
            "pending_host": None,
        }

        # Simuler un événement CHANGE_HOST : construire un message HID++ long
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        def _make_switch_msg(change_host_index, target_host):
            """Construit un message HID++ REPORT_LONG simulant un CHANGE_HOST."""
            msg = bytearray(MSG_LONG_LEN)
            msg[0] = REPORT_LONG
            msg[2] = change_host_index  # feature index
            msg[3] = 0x00  # sw_id = 0 (notification)
            msg[5] = target_host
            return bytes(msg)

        # Configurer keyboard1 : ping OK, puis retourne un event switch hôte 1
        switch_msg1 = _make_switch_msg(5, 1)
        _reads1 = iter([switch_msg1])
        keyboard1.transport.read.side_effect = lambda *a, **kw: next(_reads1, None)
        keyboard1.transport.write.return_value = None
        keyboard1.transport.is_open = True

        # Configurer keyboard2 : ping OK, puis retourne un event switch hôte 2
        switch_msg2 = _make_switch_msg(7, 2)
        _reads2 = iter([switch_msg2])
        keyboard2.transport.read.side_effect = lambda *a, **kw: next(_reads2, None)
        keyboard2.transport.write.return_value = None
        keyboard2.transport.is_open = True

        from swigi.daemon import _watch_keyboard

        # Lancer les deux threads de surveillance
        with _fast_probe():
            t1 = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard1, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            t2 = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard2, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            t1.start()
            t2.start()

            # Attendre que les events arrivent (timeout 3s)
            events = []
            deadline = time.time() + 3.0
            while len(events) < 2 and time.time() < deadline:
                try:
                    event = event_queue.get(timeout=0.05)
                    events.append(event)
                except q_module.Empty:
                    pass

            stop_event.set()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)

        # Vérifier qu'on a bien reçu 2 SwitchEvents indépendants
        switch_events = [error for error in events if isinstance(error, _SwitchEvent)]
        self.assertEqual(len(switch_events), 2)

        hosts = {error.target_host for error in switch_events}
        self.assertIn(1, hosts)
        self.assertIn(2, hosts)

        names = {error.keyboard_name for error in switch_events}
        self.assertIn("MX Keys S", names)
        self.assertIn("MX Keys Mini", names)


class TestWatchKeyboardFiltering(unittest.TestCase):
    def test_change_host_with_non_zero_swid_is_ignored(self):
        """Un paquet CHANGE_HOST de réponse (swid!=0) ne doit pas déclencher de switch."""
        import queue as q_module
        import threading
        from swigi.constants import MSG_LONG_LEN, REPORT_LONG
        from swigi.daemon import _SwitchEvent

        event_queue = q_module.Queue()
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        keyboard = _make_keyboard(change_host_index=5, name="MX Keys S")
        keyboard.product_id = 0xB35B

        # feat=change_host_index, swid=0xA (réponse interne), newHost=1
        packet = bytearray(MSG_LONG_LEN)
        packet[0] = REPORT_LONG
        packet[2] = 5
        packet[3] = 0x0A
        packet[4] = 3
        packet[5] = 1

        reads = iter([bytes(packet), None, None, None])
        keyboard.transport.read.side_effect = lambda *a, **kw: next(reads, None)
        keyboard.transport.write.return_value = None
        keyboard.transport.is_open = True

        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        with _fast_probe():
            worker = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop_event, hunt_trigger),
                daemon=True,
            )
            worker.start()
            time.sleep(0.12)
            stop_event.set()
            worker.join(timeout=1.0)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get())

        switch_events = [event for event in events if isinstance(event, _SwitchEvent)]
        self.assertEqual(switch_events, [])


class TestMiceProbeLoop(unittest.TestCase):
    """Tests pour _mice_probe_loop : détection, remplacement, retrait, pending_host."""

    def _make_lock(self):
        import threading

        return threading.Lock()

    def test_new_mouse_added_to_list(self):
        """Nouvelle souris détectée → ajoutée à mice_list."""
        new_m = _make_mouse(name="MX Master 4")
        new_m.product_id = 0xB042
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[new_m]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=None),
        ):
            import threading
            from swigi.daemon import _mice_probe_loop

            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1)

        self.assertGreater(len(mice_list), 0)

    def test_dead_mouse_replaced(self):
        """Souris avec transport fermé → remplacée par nouvelle instance."""
        old_m = _make_mouse(name="MX Master 4")
        old_m.product_id = 0xB042
        old_m.transport.is_open = False

        new_m = _make_mouse(name="MX Master 4")
        new_m.product_id = 0xB042
        new_m.transport.is_open = True

        mice_list = [old_m]
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[new_m]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=None),
        ):
            import threading
            from swigi.daemon import _mice_probe_loop

            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1)

        # new_m doit être dans la liste, pas old_m
        self.assertIn(new_m, mice_list)
        self.assertNotIn(old_m, mice_list)

    def test_dead_mouse_removed_when_not_found(self):
        """Souris morte non retrouvée par find_all_devices → retirée."""
        dead_m = _make_mouse(name="MX Master 4")
        dead_m.product_id = 0xB042
        dead_m.transport.is_open = False

        mice_list = [dead_m]
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[]),
            patch("swigi.daemon.notify"),
        ):
            import threading
            from swigi.daemon import _mice_probe_loop

            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1)

        self.assertNotIn(dead_m, mice_list)

    def test_pending_host_applied_at_reconnect(self):
        """pending_host appliqué quand nouvelle souris connectée."""
        new_m = _make_mouse(name="MX Master 4")
        new_m.product_id = 0xB042

        mice_list = []
        state = {"pending_host": (1, time.time() + 60), "mouse": None, "mice": []}
        lock = self._make_lock()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[new_m]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=1),
        ):
            import threading
            from swigi.daemon import _mice_probe_loop

            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1)

        # pending_host effacé car sync OK
        self.assertIsNone(state["pending_host"])

    def test_existing_mouse_rechecked_when_pending_host_active(self):
        """Souris déjà dans mice_list + pending_host actif → pass 2b corrige la désync."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        mice_list = [mouse]  # déjà dans la liste (pas "new")
        state = {
            "pending_host": (1, time.time() + 60),
            "mouse": "MX Master 4",
            "mice": ["MX Master 4"],
            "last_change_host_at": time.time(),  # switch SwiGi récent → correction autorisée
        }
        lock = self._make_lock()

        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        # Souris sur hôte 0, pending dit hôte 1 → correction attendue
        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[mouse]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1)

        self.assertIn(
            1,
            sent,
            "Correction pending_host non envoyée pour souris existante (pass 2b)",
        )

    def test_existing_mouse_not_rechecked_when_new_mice_found(self):
        """Si des nouvelles souris sont trouvées, pass 2b est skippé (pass 2 suffit)."""
        existing_m = _make_mouse(name="MX Vertical")
        existing_m.product_id = 0xB021
        new_m = _make_mouse(name="MX Master 4")
        new_m.product_id = 0xB042

        mice_list = [existing_m]
        state = {
            "pending_host": (1, time.time() + 60),
            "mouse": "MX Vertical",
            "mice": ["MX Vertical"],
        }
        lock = self._make_lock()

        # new_m est sync (hôte 1) → pending cleared by pass 2
        # existing_m ne devrait PAS recevoir de correction (pass 2b skippé)
        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[existing_m, new_m]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.08)
            stop.set()
            t.join(timeout=1)

        # Pas de correction envoyée (sync OK pour la new_m)
        mock_send.assert_not_called()
        self.assertIsNone(state["pending_host"])


# ── Fix #1 : compare-and-swap pending_host post-I/O ──────────────────────────


class TestPendingHostCompareAndSwap(unittest.TestCase):
    """Vérifie que pending_host modifié pendant le I/O de get_current_host est ignoré."""

    def test_pending_host_cleared_during_io_abandons_correction(self):
        """Nouveau switch pendant I/O → pending_host=None → correction abandonnée."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Master 4"}

        def slow_get_host(*args):
            state["pending_host"] = None  # switch suivant a effacé pending
            return 0  # souris encore sur hôte 0

        with (
            patch("swigi.daemon.get_current_host", side_effect=slow_get_host),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        mouse.close.assert_not_called()

    def test_pending_host_changed_to_new_target_during_io_abandons(self):
        """Switch vers hôte 2 pendant I/O (cible était 1) → correction vers 1 abandonnée."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Master 4"}

        def slow_get_host(*args):
            state["pending_host"] = (2, time.time() + 60)  # switch vers hôte 2
            return 0

        with (
            patch("swigi.daemon.get_current_host", side_effect=slow_get_host),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        # Le nouveau pending (hôte 2) est préservé
        self.assertEqual(state["pending_host"][0], 2)

    def test_pending_host_same_target_during_io_correction_fires(self):
        """Même cible pendant I/O → correction normale appliquée."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Master 4", "last_change_host_at": time.time()}

        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        mock_send.assert_called_once()

    def test_3hosts_pending_changed_from_1_to_2_during_io(self):
        """3 Macs : cible change de 1→2 pendant I/O, correction vers 1 abandonnée."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Master 4"}

        def slow_get_host(*args):
            state["pending_host"] = (2, time.time() + 60)
            return 0

        with (
            patch("swigi.daemon.get_current_host", side_effect=slow_get_host),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            _check_and_apply_pending_host(mouse, state)

        mock_send.assert_not_called()
        self.assertEqual(state["pending_host"][0], 2)


# ── Fix #2 : num_hosts dynamique dans _watch_keyboard ────────────────────────


class TestWatchKeyboardNotificationParsing(unittest.TestCase):
    """Vérifie le parsing de la notification CHANGE_HOST avec num_hosts depuis raw[4]."""

    def _run_watch_and_collect(self, switch_msg):
        """Lance _watch_keyboard, lui soumet un message, retourne l'event reçu."""
        from swigi.daemon import _SwitchEvent

        event_queue = __import__("queue").Queue()
        stop = threading.Event()
        hunt = threading.Event()
        keyboard = _make_keyboard(change_host_index=5, name="MX Keys S")
        keyboard.product_id = 0xB35B
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        # Retourner le message une fois, puis None indéfiniment (évite StopIteration dans thread)
        _reads = iter([switch_msg])
        keyboard.transport.read.side_effect = lambda *a, **kw: next(_reads, None)
        keyboard.transport.write.return_value = None
        keyboard.transport.is_open = True

        with _fast_probe():
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            events = []
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    events.append(event_queue.get(timeout=0.05))
                    break
                except __import__("queue").Empty:
                    pass
            stop.set()
            t.join(timeout=1.5)
        return [error for error in events if isinstance(error, _SwitchEvent)]

    def _make_notif(self, change_host_index, num_hosts, target_host):
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        msg = bytearray(MSG_LONG_LEN)
        msg[0] = REPORT_LONG
        msg[2] = change_host_index
        msg[3] = 0x00  # sw_id = 0 (notification)
        msg[4] = num_hosts
        msg[5] = target_host
        return bytes(msg)

    def test_valid_host_3hosts_accepted(self):
        """num_hosts=3, target=1 → switch accepté."""
        msg = self._make_notif(5, 3, 1)
        events = self._run_watch_and_collect(msg)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target_host, 1)

    def test_valid_host_2hosts_accepted(self):
        """num_hosts=2, target=1 → switch accepté."""
        msg = self._make_notif(5, 2, 1)
        events = self._run_watch_and_collect(msg)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target_host, 1)

    def test_invalid_host_equals_num_hosts_dropped(self):
        """num_hosts=2, target=2 → hôte invalide (2 >= 2) → switch ignoré."""
        msg = self._make_notif(5, 2, 2)
        events = self._run_watch_and_collect(msg)
        self.assertEqual(len(events), 0)

    def test_num_hosts_zero_fallback_to_3(self):
        """num_hosts=0 dans message → fallback 3, target=2 → switch accepté."""
        msg = self._make_notif(5, 0, 2)
        events = self._run_watch_and_collect(msg)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target_host, 2)

    def test_all_three_hosts_reachable(self):
        """Chaque hôte 0/1/2 sur 3 hosts → switch accepté."""
        for target in (0, 1, 2):
            msg = self._make_notif(5, 3, target)
            events = self._run_watch_and_collect(msg)
            self.assertEqual(len(events), 1, f"hôte {target} non reçu")
            self.assertEqual(events[0].target_host, target)

    def test_nonzero_swid_notification_ignored(self):
        """sw_id != 0 → réponse interne HID++, ne doit pas déclencher de switch."""
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        msg = bytearray(MSG_LONG_LEN)
        msg[0] = REPORT_LONG
        msg[2] = 5            # change_host_index
        msg[3] = 0x01         # sw_id = 1 (réponse requête, pas notification bouton)
        msg[4] = 3            # num_hosts
        msg[5] = 1            # target = hôte 2 (base-0)
        events = self._run_watch_and_collect(bytes(msg))
        self.assertEqual(len(events), 0, "sw_id=1 doit être ignoré")


class TestEasySwitchLogNumbers(unittest.TestCase):
    """Vérifie que le numéro de touche (1/2/3) dans les logs est correct.

    '★ Touche Easy-Switch N pressée' doit afficher N = target_host + 1 (base-1).
    """

    def _capture_switch_log(self, target_host_base0, *, debug_raw_packets=False):
        """Lance _watch_keyboard avec une notification CHANGE_HOST et capture les logs."""
        import logging
        import queue as q_module
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN
        from swigi.daemon import _watch_keyboard, _SwitchEvent

        msg = bytearray(MSG_LONG_LEN)
        msg[0] = REPORT_LONG
        msg[2] = 5                    # change_host_index
        msg[3] = 0x00                 # sw_id = 0
        msg[4] = 3                    # num_hosts
        msg[5] = target_host_base0
        pkt = bytes(msg)

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()
        keyboard = _make_keyboard(change_host_index=5, name="MX Keys S")
        keyboard.product_id = 0xB35B
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
            "debug_raw_packets": debug_raw_packets,
        }
        _reads = iter([pkt])
        keyboard.transport.read.side_effect = lambda *a, **kw: next(_reads, None)
        keyboard.transport.write.return_value = None
        keyboard.transport.is_open = True

        log_records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                log_records.append(record.getMessage())

        handler = _Capture()
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("swigi.daemon")
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            with _fast_probe():
                t = threading.Thread(
                    target=_watch_keyboard,
                    args=(keyboard, event_queue, state, stop, hunt),
                    daemon=True,
                )
                t.start()
                deadline = time.time() + 1.5
                while time.time() < deadline:
                    try:
                        event_queue.get(timeout=0.05)
                        break
                    except q_module.Empty:
                        pass
                stop.set()
                t.join(timeout=1.5)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        return log_records

    def test_easy_switch_1_log_number(self):
        """Hôte 0 (base-0) → log '★ Touche Easy-Switch 1 pressée'."""
        logs = self._capture_switch_log(0)
        matching = [l for l in logs if "★ Touche Easy-Switch" in l]
        self.assertTrue(matching, f"Aucun log Easy-Switch trouvé. Logs: {logs[:5]}")
        self.assertIn("Easy-Switch 1 pressée", matching[0])

    def test_easy_switch_2_log_number(self):
        """Hôte 1 (base-0) → log '★ Touche Easy-Switch 2 pressée'."""
        logs = self._capture_switch_log(1)
        matching = [l for l in logs if "★ Touche Easy-Switch" in l]
        self.assertTrue(matching, f"Aucun log Easy-Switch trouvé. Logs: {logs[:5]}")
        self.assertIn("Easy-Switch 2 pressée", matching[0])

    def test_easy_switch_3_log_number(self):
        """Hôte 2 (base-0) → log '★ Touche Easy-Switch 3 pressée'."""
        logs = self._capture_switch_log(2)
        matching = [l for l in logs if "★ Touche Easy-Switch" in l]
        self.assertTrue(matching, f"Aucun log Easy-Switch trouvé. Logs: {logs[:5]}")
        self.assertIn("Easy-Switch 3 pressée", matching[0])

    def test_pkt_dump_logged_when_enabled(self):
        """Le dump PKT n'apparaît que si debug_raw_packets est activé."""
        logs = self._capture_switch_log(1, debug_raw_packets=True)
        pkt_logs = [l for l in logs if "PKT report=" in l]
        self.assertTrue(pkt_logs, "Aucun PKT dump DEBUG trouvé alors que debug_raw_packets=True")

    def test_pkt_dump_disabled_by_default(self):
        """Par défaut, les dumps PKT bruts sont désactivés pour limiter le bruit."""
        logs = self._capture_switch_log(1)
        pkt_logs = [l for l in logs if "PKT report=" in l]
        self.assertFalse(pkt_logs, "Dump PKT présent alors qu'il doit être désactivé par défaut")

    def test_drain_buffer_read_exception_does_not_exit_immediately(self):
        """Drain buffer : une exception read ne doit pas sortir immédiatement.

        Si le ping échoue ET les reads lèvent TransportError, le drain doit continuer
        jusqu'à 10 exceptions consécutives (pas sortir à la première).
        Le test vérifie qu'un paquet CHANGE_HOST présent après 3 exceptions est capturé.
        """
        import logging
        import queue as q_module
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN
        from swigi.daemon import _watch_keyboard, _SwitchEvent
        from swigi.transport import TransportError

        msg = bytearray(MSG_LONG_LEN)
        msg[0] = REPORT_LONG
        msg[2] = 5
        msg[3] = 0x00
        msg[4] = 3
        msg[5] = 1   # Easy-Switch 2
        pkt = bytes(msg)

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()
        keyboard = _make_keyboard(change_host_index=5, name="MX Keys S Drain")
        keyboard.product_id = 0xB35B
        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        # Ping échoue → drain buffer activé
        keyboard.transport.write.side_effect = TransportError("ping fail")

        # Drain : 3 exceptions puis le paquet CHANGE_HOST puis None
        drain_calls = [
            TransportError("drain fail 1"),
            TransportError("drain fail 2"),
            TransportError("drain fail 3"),
            pkt,
            None,
        ]
        call_idx = [0]

        def _drain_read(*a, **kw):
            r = drain_calls[call_idx[0]]
            call_idx[0] = min(call_idx[0] + 1, len(drain_calls) - 1)
            if isinstance(r, Exception):
                raise r
            return r

        keyboard.transport.read.side_effect = _drain_read
        keyboard.transport.is_open = True

        # Reconnect après drain : keyboard2 pour éviter boucle infinie
        keyboard2 = _make_keyboard(change_host_index=5, name="MX Keys S Drain")
        keyboard2.product_id = 0xB35B
        keyboard2.transport.write.return_value = None
        keyboard2.transport.read.return_value = None
        keyboard2.transport.is_open = True

        with (
            _fast_probe(),
            patch("swigi.daemon._find_keyboard_by_product_id", return_value=keyboard2),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            events = []
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    events.append(event_queue.get(timeout=0.05))
                    break
                except q_module.Empty:
                    pass
            stop.set()
            t.join(timeout=2.0)

        switch_events = [e for e in events if isinstance(e, _SwitchEvent)]
        self.assertEqual(
            len(switch_events), 1,
            "CHANGE_HOST après 3 exceptions de drain doit être capturé "
            f"(got events={events})",
        )
        self.assertEqual(switch_events[0].target_host, 1)


# ── Fix #3 : hunt interval réel 1s (pas 6s) ──────────────────────────────────


class TestHuntIntervalTiming(unittest.TestCase):
    """Vérifie que _mice_probe_loop probe toutes les ~1s en hunt mode, pas 6s."""

    def test_hunt_mode_probes_at_1s_not_6s(self):
        """En hunt mode, ≥5 probes doivent se produire rapidement (interval=0.02s)."""
        probe_count = 0
        enough_probes = threading.Event()

        def fake_find(*args):
            nonlocal probe_count
            probe_count += 1
            if probe_count >= 5:
                enough_probes.set()
            return []

        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = threading.Lock()
        stop = threading.Event()
        hunt = threading.Event()
        hunt.set()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", side_effect=fake_find),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            got_enough = enough_probes.wait(timeout=2.0)
            stop.set()
            t.join(timeout=1)

        self.assertTrue(
            got_enough,
            f"Hunt mode trop lent : seulement {probe_count} probe(s) avant timeout 2s "
            f"(interval=0.02s, attendu ≥5). Bug probable : wait timeout non adapté.",
        )

    def test_hunt_mode_intervals_under_2s(self):
        """Intervalles entre probes en hunt mode doivent être bien inférieurs à l'interval normal."""
        call_times = []
        enough_probes = threading.Event()

        def fake_find(*args):
            call_times.append(time.time())
            if len(call_times) >= 5:
                enough_probes.set()
            return []

        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = threading.Lock()
        stop = threading.Event()
        hunt = threading.Event()
        hunt.set()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", side_effect=fake_find),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            enough_probes.wait(timeout=2.0)
            stop.set()
            t.join(timeout=1)

        if len(call_times) >= 2:
            intervals = [
                call_times[i + 1] - call_times[i] for i in range(len(call_times) - 1)
            ]
            max_interval = max(intervals)
            # 0.3s < normal interval (0.1s patched) — détecte une régression sans être flaky
            self.assertLess(
                max_interval,
                0.3,
                f"Intervalle max = {max_interval:.3f}s (attendu < 0.3s en hunt mode avec interval=0.02s)",
            )

    def test_normal_mode_probes_at_5s(self):
        """Hors hunt mode, probe toutes les ~0.1s (pas 0.02s — éviter CPU busy-loop)."""
        call_times = []

        def fake_find(*args):
            call_times.append(time.time())
            return []

        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = threading.Lock()
        stop = threading.Event()
        hunt = threading.Event()
        # hunt NOT set → mode normal

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", side_effect=fake_find),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.05)  # 0.05s → moins d'un intervalle normal (0.1s)
            stop.set()
            t.join(timeout=1)

        # En mode normal, 0.05s < 0.1s : au plus 1 probe.
        self.assertLessEqual(
            len(call_times),
            1,
            f"Mode normal : {len(call_times)} probes en 0.05s (devrait être ≤1)",
        )


# ── Fix #4 : démarrage sans souris ───────────────────────────────────────────


class TestStartupWithoutMouse(unittest.TestCase):
    """Vérifie que le probe loop trouve la souris même absente au démarrage."""

    def test_mouse_absent_at_startup_found_later(self):
        """Probe loop trouve la souris après qu'elle arrive (était sur autre Mac)."""
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = threading.Lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        call_count = [0]

        def fake_find(*args):
            call_count[0] += 1
            return [mouse] if call_count[0] >= 3 else []  # souris arrive au 3e probe

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", side_effect=fake_find),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=None),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()  # hunt mode : 0.02s entre probes

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.2)  # 0.2s → ≥3 probes à 0.02s d'intervalle
            stop.set()
            t.join(timeout=1)

        self.assertIn(mouse, mice_list, "Souris jamais trouvée après arrivée tardive")
        self.assertEqual(state["mouse"], "MX Master 4")

    def test_mouse_absent_then_arrives_pending_host_applied(self):
        """Souris absente au démarrage, pending_host actif → correction appliquée à l'arrivée."""
        mice_list = []
        # pending_host = 0 (Mac A) — souris doit revenir ici
        state = {"pending_host": (0, time.time() + 60), "mouse": None, "mice": [], "last_change_host_at": time.time()}
        lock = threading.Lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        call_count = [0]
        sent_hosts = []

        def fake_find(*args):
            call_count[0] += 1
            return [mouse] if call_count[0] >= 2 else []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent_hosts.append(target_host)

        # Simuler : souris sur hôte 1 (Mac B), doit aller vers hôte 0 (Mac A)
        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", side_effect=fake_find),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.20)
            stop.set()
            t.join(timeout=1)

        self.assertIn(
            0,
            sent_hosts,
            "Correction pending_host non envoyée à l'arrivée de la souris",
        )


# ── Scénario 3 Macs complet ───────────────────────────────────────────────────


class TestThreeMacFullScenario(unittest.TestCase):
    """Tests d'intégration : chaîne de switch A(0)→B(1)→C(2)→A(0) avec 3 SwiGi."""

    def _make_lock(self):
        return threading.Lock()

    def test_switch_A_to_B_send_mouse_host1(self):
        """Mac A switch vers B : souris reçoit CHANGE_HOST vers hôte 1."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042
        mice_list = [mouse]
        state = {"pending_host": None, "mouse": "MX Master 4", "mice": ["MX Master 4"]}
        lock = self._make_lock()
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)
            mouse.transport.is_open = False

        with patch("swigi.daemon.send_change_host", side_effect=fake_send):
            _send_to_all_mice(mice_list, 1, state, lock)

        self.assertEqual(sent, [1])
        self.assertEqual(state["pending_host"][0], 1)
        self.assertEqual(mice_list, [])

    def test_switch_B_to_C_send_mouse_host2(self):
        """Mac B switch vers C : souris reçoit CHANGE_HOST vers hôte 2."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042
        mice_list = [mouse]
        state = {"pending_host": None, "mouse": "MX Master 4", "mice": ["MX Master 4"]}
        lock = self._make_lock()
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)
            mouse.transport.is_open = False

        with patch("swigi.daemon.send_change_host", side_effect=fake_send):
            _send_to_all_mice(mice_list, 2, state, lock)

        self.assertEqual(sent, [2])

    def test_switch_C_to_A_mouse_stuck_B_probe_corrects(self):
        """Retour C→A : souris bloquée sur B (hôte 1), probe la corrige vers hôte 0."""
        mice_list = []
        state = {"pending_host": (0, time.time() + 60), "mouse": None, "mice": [], "last_change_host_at": time.time()}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[mouse]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.08)
            stop.set()
            t.join(timeout=1)

        self.assertIn(0, sent, "Correction vers hôte 0 (Mac A) non envoyée")

    def test_3mac_round_trip_pending_host_chain(self):
        """A→B→C→A : vérifier la chaîne complète de pending_host sur Mac A."""
        # Étape 1 : Mac A envoie souris vers B lors du switch A→B
        mouse_AB = _make_mouse(name="MX Master 4")
        mouse_AB.product_id = 0xB042
        mice_A = [mouse_AB]
        state_A = {
            "pending_host": None,
            "mouse": "MX Master 4",
            "mice": ["MX Master 4"],
        }
        lock_A = self._make_lock()
        sent_A = []

        def fake_send_A(transport, devnum, feat_idx, target_host):
            sent_A.append(target_host)
            mouse_AB.transport.is_open = False

        with patch("swigi.daemon.send_change_host", side_effect=fake_send_A):
            _send_to_all_mice(mice_A, 1, state_A, lock_A)

        self.assertEqual(sent_A[-1], 1)
        self.assertEqual(state_A["pending_host"][0], 1)

        # Étape 2 : clavier revient sur Mac A, resync pending_host
        keyboard = _make_keyboard(name="MX Keys S")
        with patch("swigi.daemon.get_current_host", return_value=0):
            from swigi.daemon import _resync_pending_host_from_keyboard

            _resync_pending_host_from_keyboard(keyboard, state_A)

        self.assertEqual(state_A["pending_host"][0], 0)

        # Étape 3 : souris reconnectée sur Mac A, pas de désync (déjà sur hôte 0)
        mouse_return = _make_mouse(name="MX Master 4")
        mouse_return.product_id = 0xB042
        sent_correction = []

        def fake_send_correction(transport, devnum, feat_idx, target_host):
            sent_correction.append(target_host)

        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host", side_effect=fake_send_correction),
        ):
            result = _check_and_apply_pending_host(mouse_return, state_A)

        self.assertFalse(result, "Aucune correction attendue — souris déjà sur Mac A")
        self.assertEqual(sent_correction, [], "Correction envoyée à tort")
        self.assertIsNone(state_A["pending_host"])

    def test_3mac_desync_corrected_on_return(self):
        """Retour sur Mac A : souris bloquée sur hôte 2 (Mac C) → correction vers 0."""
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(2), "mouse": None, "last_change_host_at": time.time()}

        # Clavier revenu sur hôte 0
        with patch("swigi.daemon.get_current_host", return_value=0):
            from swigi.daemon import _resync_pending_host_from_keyboard

            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertEqual(state["pending_host"][0], 0)

        # Souris sur hôte 2 → correction vers 0
        mouse = _make_mouse()
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        with (
            patch("swigi.daemon.get_current_host", return_value=2),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        self.assertEqual(sent, [0])

    def test_two_mice_both_sent_on_switch(self):
        """2 souris présentes (2 périphériques Logitech) → les deux reçoivent CHANGE_HOST."""
        m1 = _make_mouse(change_host_index=9, name="MX Master 4")
        m1.product_id = 0xB042
        m2 = _make_mouse(change_host_index=11, name="MX Anywhere 3")
        m2.product_id = 0xB028
        mice_list = [m1, m2]
        state = {
            "pending_host": None,
            "mouse": "MX Master 4",
            "mice": ["MX Master 4", "MX Anywhere 3"],
        }
        lock = self._make_lock()

        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append((feat_idx, target_host))
            transport.is_open = False  # not used but realistic

        with patch("swigi.daemon.send_change_host", side_effect=fake_send):
            _send_to_all_mice(mice_list, 2, state, lock)

        feat_idxs = [s[0] for s in sent]
        targets = [s[1] for s in sent]
        self.assertIn(9, feat_idxs)
        self.assertIn(11, feat_idxs)
        self.assertEqual(set(targets), {2})
        self.assertEqual(mice_list, [])

    def test_switch_while_mice_list_empty_pending_host_set(self):
        """Switch avec mice_list vide (souris déjà partie) : pending_host quand même fixé."""
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice_list, 1, state, lock)

        mock_send.assert_not_called()  # aucune souris à envoyer
        self.assertEqual(state["pending_host"][0], 1)  # mais pending fixé
        self.assertIsNone(state["mouse"])

    def test_probe_finds_mouse_and_applies_pending_after_empty_switch(self):
        """Après switch avec mice_list vide, probe loop trouve la souris et corrige."""
        mice_list = []
        state = {"pending_host": (1, time.time() + 60), "mouse": None, "mice": [], "last_change_host_at": time.time()}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        # Souris sur hôte 0 (Mac A), pending dit hôte 1 (Mac B)
        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[mouse]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.08)
            stop.set()
            t.join(timeout=1)

        self.assertIn(1, sent, "Correction vers hôte 1 non envoyée malgré pending_host")

    def test_rapid_switch_second_supersedes_first(self):
        """Switch rapide A→B puis B→C : la 2e cible (C) doit être préservée dans pending."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Master 4", "mice": ["MX Master 4"]}
        lock = self._make_lock()

        def fake_send_1(transport, devnum, feat_idx, target_host):
            mouse.transport.is_open = False

        with patch("swigi.daemon.send_change_host", side_effect=fake_send_1):
            _send_to_all_mice(mice, 1, state, lock)  # switch vers B

        self.assertEqual(state["pending_host"][0], 1)

        # 2ème switch — mice_list est vide
        with patch("swigi.daemon.send_change_host"):
            _send_to_all_mice(mice, 2, state, lock)  # switch vers C

        self.assertEqual(
            state["pending_host"][0], 2, "pending_host doit refléter la 2e cible (C)"
        )

    def test_hunt_probe_after_switch_finds_mouse(self):
        """Après switch, hunt_trigger set → probe loop détecte la souris rapidement."""
        mice_list = []
        state = {"pending_host": (1, time.time() + 60), "mouse": None, "mice": []}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        arrival = [None]
        found_event = threading.Event()
        call_count = [0]

        def fake_find(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # 1er probe : pas encore là
            arrival[0] = time.time()
            found_event.set()
            return [mouse]

        start = time.time()

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", side_effect=fake_find),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=1),
        ):  # sync OK
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            # Attendre l'arrivée de la souris (event-based, pas sleep fixe)
            found_event.wait(timeout=1.0)
            stop.set()
            t.join(timeout=1)

        self.assertIsNotNone(arrival[0], "Souris jamais trouvée")
        elapsed = arrival[0] - start
        # En hunt mode (interval=0.02s), 2 probes = ~0.04-0.06s — 0.25s est très généreux
        self.assertLess(
            elapsed, 0.25, f"Souris trouvée trop tard ({elapsed:.3f}s, attendu < 0.25s)"
        )
        self.assertIsNone(
            state["pending_host"], "pending_host non effacé après sync OK"
        )

    def test_state_mouse_updated_after_probe(self):
        """state['mouse'] mis à jour après détection souris par probe loop."""
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[mouse]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=None),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.05)
            stop.set()
            t.join(timeout=1)

        self.assertEqual(state["mouse"], "MX Master 4")
        self.assertIn("MX Master 4", state["mice"])


# ── _update_kb_state ──────────────────────────────────────────────────────────


class TestUpdateKbState(unittest.TestCase):
    def test_first_active_kb_name_set(self):
        state = {"keyboards": {0xB35B: {"name": "MX Keys S", "ok": True}}}
        from swigi.daemon import _update_keyboard_state

        _update_keyboard_state(state)
        self.assertEqual(state["keyboard"], "MX Keys S")

    def test_skips_down_kb_returns_active(self):
        state = {
            "keyboards": {
                0xB35B: {"name": "MX Keys S", "ok": False},
                0xB361: {"name": "MX Keys Mini", "ok": True},
            }
        }
        from swigi.daemon import _update_keyboard_state

        _update_keyboard_state(state)
        self.assertEqual(state["keyboard"], "MX Keys Mini")

    def test_all_down_sets_none(self):
        state = {"keyboards": {0xB35B: {"name": "MX Keys S", "ok": False}}}
        from swigi.daemon import _update_keyboard_state

        _update_keyboard_state(state)
        self.assertIsNone(state["keyboard"])

    def test_empty_kbs_sets_none(self):
        state = {"keyboards": {}}
        from swigi.daemon import _update_keyboard_state

        _update_keyboard_state(state)
        self.assertIsNone(state["keyboard"])

    def test_with_state_lock(self):
        state = {
            "_state_lock": threading.Lock(),
            "keyboards": {0xB35B: {"name": "MX Keys S", "ok": True}},
        }
        from swigi.daemon import _update_keyboard_state

        _update_keyboard_state(state)
        self.assertEqual(state["keyboard"], "MX Keys S")

    def test_no_kbs_key_sets_none(self):
        state = {}
        from swigi.daemon import _update_keyboard_state

        _update_keyboard_state(state)
        self.assertIsNone(state["keyboard"])


# ── _watch_keyboard : ping fail + reconnect ───────────────────────────────────


class TestWatchKeyboardPingFailReconnect(unittest.TestCase):
    """Teste le chemin ping-fail → reconnect → KbReconnected (critique pour le switch)."""

    def test_ping_fail_emits_kb_reconnected_and_sets_hunt(self):
        """Ping fail → clavier marqué down → reconnect → KbReconnected dans queue."""
        import queue as q_module
        from swigi.daemon import _KeyboardReconnected

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        old_keyboard = _make_keyboard(name="MX Keys S")
        old_keyboard.product_id = 0xB35B
        # Premier write OK, second lève TransportError (ping fail)
        write_calls = [0]

        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("ping dead")

        old_keyboard.transport.write.side_effect = write_side
        old_keyboard.transport.read.side_effect = lambda *a, **kw: None
        old_keyboard.transport.is_open = True

        new_keyboard = _make_keyboard(name="MX Keys S")
        new_keyboard.product_id = 0xB35B
        new_keyboard.transport.write.return_value = None
        new_keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {
                old_keyboard.product_id: {"name": old_keyboard.name, "ok": True}
            },
            "pending_host": None,
        }

        with (
            _fast_probe(),
            patch(
                "swigi.daemon._find_keyboard_by_product_id", return_value=new_keyboard
            ),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(old_keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            events = []
            deadline = time.time() + 1.5
            while time.time() < deadline:
                try:
                    events.append(event_queue.get(timeout=0.05))
                    break
                except q_module.Empty:
                    pass
            stop.set()
            t.join(timeout=1.5)

        reconnected = [
            error for error in events if isinstance(error, _KeyboardReconnected)
        ]
        self.assertGreater(
            len(reconnected), 0, "_KeyboardReconnected non émis après ping fail"
        )
        self.assertTrue(state["keyboards"][new_keyboard.product_id]["ok"])

    def test_ping_fail_post_switch_no_disconnect_notify(self):
        """Ping fail juste après un switch → notification 'déconnecté' supprimée (normal)."""
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B

        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        switch_msg = bytearray(MSG_LONG_LEN)
        switch_msg[0] = REPORT_LONG
        switch_msg[2] = 5
        switch_msg[3] = 0x00
        switch_msg[4] = 3
        switch_msg[5] = 1
        _reads = iter([bytes(switch_msg)])

        write_calls = [0]

        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("post-switch disconnect")

        keyboard.transport.write.side_effect = write_side
        keyboard.transport.read.side_effect = lambda *a, **kw: next(_reads, None)
        keyboard.transport.is_open = True

        new_keyboard = _make_keyboard(name="MX Keys S")
        new_keyboard.product_id = 0xB35B
        new_keyboard.transport.write.return_value = None
        new_keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        notifications = []
        with (
            _fast_probe(),
            patch(
                "swigi.daemon._find_keyboard_by_product_id", return_value=new_keyboard
            ),
            patch("swigi.daemon.get_current_host", return_value=1),
            patch(
                "swigi.daemon.notify",
                side_effect=lambda msg, *a: notifications.append(msg),
            ),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1.5)

        disconnect_notifs = [n for n in notifications if "déconnecté" in n.lower()]
        self.assertEqual(
            disconnect_notifs,
            [],
            f"Notification 'déconnecté' inattendue post-switch : {disconnect_notifs}",
        )

    def test_ping_fail_reconnect_triggers_hunt(self):
        """Reconnect clavier → hunt_trigger.set() pour probe rapide souris."""
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        old_keyboard = _make_keyboard(name="MX Keys S")
        old_keyboard.product_id = 0xB35B
        write_calls = [0]

        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("dead")

        old_keyboard.transport.write.side_effect = write_side
        old_keyboard.transport.read.side_effect = lambda *a, **kw: None

        new_keyboard = _make_keyboard(name="MX Keys S")
        new_keyboard.product_id = 0xB35B
        new_keyboard.transport.write.return_value = None
        new_keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {
                old_keyboard.product_id: {"name": old_keyboard.name, "ok": True}
            },
            "pending_host": None,
        }

        with (
            _fast_probe(),
            patch(
                "swigi.daemon._find_keyboard_by_product_id", return_value=new_keyboard
            ),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(old_keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            hunt.wait(timeout=1.5)
            stop.set()
            t.join(timeout=1.5)

        self.assertTrue(hunt.is_set(), "hunt_trigger non levé après reconnect clavier")

    def test_read_transport_error_in_window_breaks_gracefully(self):
        """TransportError pendant read dans fenêtre 80ms → break, pas de crash."""
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B
        read_calls = [0]

        def read_side(*a, **kw):
            read_calls[0] += 1
            if read_calls[0] == 1:
                raise TransportError("read dead")
            return None

        keyboard.transport.write.return_value = None
        keyboard.transport.read.side_effect = read_side

        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        with _fast_probe():
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            time.sleep(0.05)
            stop.set()
            t.join(timeout=1)
        # Pas d'exception propagée — test passe si le thread s'est terminé proprement

    def test_bad_rid_message_skipped(self):
        """Message avec rid inconnu (0xFF) → ignoré, pas de crash."""
        import queue as q_module
        from swigi.constants import MSG_LONG_LEN

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B

        bad_msg = bytearray(MSG_LONG_LEN)
        bad_msg[0] = 0xFF  # rid inconnu
        _reads = iter([bytes(bad_msg)])
        keyboard.transport.write.return_value = None
        keyboard.transport.read.side_effect = lambda *a, **kw: next(_reads, None)

        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        with _fast_probe():
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            time.sleep(0.05)
            stop.set()
            t.join(timeout=1)
        self.assertTrue(
            event_queue.empty(), "Aucun event attendu pour message rid=0xFF"
        )

    def test_ping_fail_reconnect_backoff_multiple_attempts(self):
        """Reconnect après 2 échecs (backoff) → finalement reconnecté, hunt levé."""
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        old_keyboard = _make_keyboard(name="MX Keys S")
        old_keyboard.product_id = 0xB35B
        write_calls = [0]

        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("dead")

        old_keyboard.transport.write.side_effect = write_side
        old_keyboard.transport.read.side_effect = lambda *a, **kw: None

        new_keyboard = _make_keyboard(name="MX Keys S")
        new_keyboard.product_id = 0xB35B
        new_keyboard.transport.write.return_value = None
        new_keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {
                old_keyboard.product_id: {"name": old_keyboard.name, "ok": True}
            },
            "pending_host": None,
        }

        # 2 tentatives None → backoff lines 272-273, puis succès
        attempts = [0]

        def find_side(pid):
            attempts[0] += 1
            return None if attempts[0] < 3 else new_keyboard

        with (
            _fast_probe(),
            patch("swigi.daemon._find_keyboard_by_product_id", side_effect=find_side),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(old_keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            hunt.wait(timeout=1.5)
            stop.set()
            t.join(timeout=1.5)

        self.assertTrue(
            hunt.is_set(), "hunt_trigger non levé après reconnect avec backoff"
        )
        self.assertGreaterEqual(attempts[0], 3)

    def test_ping_fail_stop_during_reconnect_exits_cleanly(self):
        """Stop pendant reconnect (find toujours None) → thread se termine proprement."""
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        old_keyboard = _make_keyboard(name="MX Keys S")
        old_keyboard.product_id = 0xB35B
        write_calls = [0]

        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("dead")

        old_keyboard.transport.write.side_effect = write_side
        old_keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {
                old_keyboard.product_id: {"name": old_keyboard.name, "ok": True}
            },
            "pending_host": None,
        }

        def stop_after():
            # Doit firer APRÈS le second ping fail (~0.17s) mais bien avant 1s
            time.sleep(0.3)
            stop.set()

        stopper = threading.Thread(target=stop_after, daemon=True)

        with (
            _fast_probe(),
            patch("swigi.daemon._find_keyboard_by_product_id", return_value=None),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(old_keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            stopper.start()
            t.join(timeout=2.0)
            stopper.join(timeout=1)

        self.assertFalse(t.is_alive(), "Thread _watch_keyboard bloqué (stop ignoré)")
        self.assertFalse(state["keyboards"][old_keyboard.product_id]["ok"])

    def test_non_switch_notification_logged_not_dispatched(self):
        """Notification feature inconnue, sw_id=0 → loggée, aucun SwitchEvent."""
        import queue as q_module
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN
        from swigi.daemon import _SwitchEvent

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        keyboard = _make_keyboard(change_host_index=5, name="MX Keys S")
        keyboard.product_id = 0xB35B

        # Feature 0x20 (pas CHANGE_HOST), sw_id=0
        notif = bytearray(MSG_LONG_LEN)
        notif[0] = REPORT_LONG
        notif[2] = 0x20  # feature différente de change_host_index=5
        notif[3] = 0x00  # sw_id = 0
        _reads = iter([bytes(notif)])
        keyboard.transport.write.return_value = None
        keyboard.transport.read.side_effect = lambda *a, **kw: next(_reads, None)

        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": None,
        }

        with _fast_probe():
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            time.sleep(0.05)
            stop.set()
            t.join(timeout=1)

        switch_events = []
        while not event_queue.empty():
            event = event_queue.get_nowait()
            if isinstance(event, _SwitchEvent):
                switch_events.append(event)
        self.assertEqual(
            switch_events, [], "SwitchEvent inattendu pour notification non-CHANGE_HOST"
        )


# ── run_daemon : intégration dispatcher ──────────────────────────────────────


class TestRunDaemon(unittest.TestCase):
    """Tests de run_daemon : initialisation état, dispatch SwitchEvent, KbReconnected."""

    def test_switch_event_dispatches_change_host_to_mouse(self):
        """SwitchEvent dans queue → _send_to_all_mice appelé, switches comptabilisé."""
        from swigi.daemon import run_daemon, _SwitchEvent

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        stop = threading.Event()
        state = {"pending_host": None}
        sent = []

        def fake_watch_kb(keyboard, event_queue, state, stop_event, hunt_trigger):
            time.sleep(0.05)
            event_queue.put(_SwitchEvent(1, keyboard.name))
            stop_event.wait()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop_event.wait()

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)
            stop.set()

        with (
            patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb),
            patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=run_daemon, args=([keyboard], [mouse], state, stop), daemon=True
            )
            t.start()
            t.join(timeout=3)

        self.assertIn(1, sent, "CHANGE_HOST vers hôte 1 non envoyé")
        self.assertGreater(state.get("switches", 0), 0)

    def test_kb_reconnected_event_updates_kb_state(self):
        """KbReconnected dans queue → state['keyboard'] mis à jour."""
        from swigi.daemon import run_daemon, _KeyboardReconnected

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B
        mouse = _make_mouse(name="MX Master 4")

        stop = threading.Event()
        state = {"pending_host": None}

        def fake_watch_kb(
            keyboard_object, event_queue, state, stop_event, hunt_trigger
        ):
            time.sleep(0.05)
            event_queue.put(_KeyboardReconnected(keyboard_object.name))
            time.sleep(0.2)
            stop_event.set()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop_event.wait()

        with (
            patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb),
            patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=run_daemon, args=([keyboard], [mouse], state, stop), daemon=True
            )
            t.start()
            t.join(timeout=3)

        self.assertIsNotNone(state.get("keyboard"))

    def test_state_initialised_with_keyboards_and_mice(self):
        """run_daemon initialise state['keyboard'], state['mouse'], state['mice']."""
        from swigi.daemon import run_daemon

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        stop = threading.Event()
        state = {"pending_host": None}

        def fake_watch_kb(
            keyboard_object, event_queue, state, stop_event, hunt_trigger
        ):
            stop_event.wait()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop.set()

        with (
            patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb),
            patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=run_daemon, args=([keyboard], [mouse], state, stop), daemon=True
            )
            t.start()
            t.join(timeout=3)

        self.assertEqual(state["keyboard"], "MX Keys S")
        self.assertEqual(state["mouse"], "MX Master 4")
        self.assertIn("MX Master 4", state["mice"])
        self.assertIn("_state_lock", state)

    def test_run_daemon_no_mice_state_mouse_is_none(self):
        """run_daemon avec mice=[] → state['mouse'] = None (démarrage sans souris)."""
        from swigi.daemon import run_daemon

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B

        stop = threading.Event()
        state = {"pending_host": None}

        def fake_watch_kb(
            keyboard_object, event_queue, state, stop_event, hunt_trigger
        ):
            stop_event.wait()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop.set()

        with (
            patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb),
            patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=run_daemon, args=([keyboard], [], state, stop), daemon=True
            )
            t.start()
            t.join(timeout=3)

        self.assertIsNone(state["mouse"])
        self.assertEqual(state["mice"], [])

    def test_two_switches_increments_counter(self):
        """Deux SwitchEvent → state['switches'] = 2."""
        from swigi.daemon import run_daemon, _SwitchEvent

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042

        stop = threading.Event()
        state = {"pending_host": None}
        switch_count = [0]

        def fake_watch_kb(
            keyboard_object, event_queue, state, stop_event, hunt_trigger
        ):
            time.sleep(0.05)
            event_queue.put(_SwitchEvent(1, keyboard_object.name))
            time.sleep(0.1)
            event_queue.put(_SwitchEvent(0, keyboard_object.name))
            time.sleep(0.2)
            stop_event.set()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop_event.wait()

        def fake_send(transport, devnum, feat_idx, target_host):
            switch_count[0] += 1

        with (
            patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb),
            patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=run_daemon, args=([keyboard], [mouse], state, stop), daemon=True
            )
            t.start()
            t.join(timeout=4)

        self.assertEqual(state.get("switches", 0), 2)


# ── Tests mouse_follow ─────────────────────────────────────────────────────────


class TestMouseFollowPreference(unittest.TestCase):
    """Tests pour la préférence mouse_follow : activer/désactiver le suivi souris."""

    def test_send_to_all_mice_always_sends_regardless_of_mouse_follow(self):
        """_send_to_all_mice envoie toujours — le check mouse_follow est dans run_daemon."""
        _mock_gui.prefs = {"mouse_follow": False, "notifications": True}
        mouse = _make_mouse()
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        lock = threading.Lock()

        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            _send_to_all_mice(mice, 1, state, lock)

        # _send_to_all_mice envoie toujours (le check est dans run_daemon)
        # Ce test vérifie le comportement au niveau de run_daemon
        mock_send.assert_called_once()

    def test_check_pending_host_clears_when_follow_disabled(self):
        """mouse_follow=False → _check_and_apply_pending_host efface pending et retourne False."""
        _mock_gui.prefs = {"mouse_follow": False, "notifications": True}
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}

        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.get_current_host") as mock_gch,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        self.assertIsNone(state["pending_host"])
        mock_gch.assert_not_called()
        mouse.close.assert_not_called()

    def test_resync_clears_pending_when_follow_disabled(self):
        """mouse_follow=False → _resync_pending_host_from_keyboard efface pending_host."""
        _mock_gui.prefs = {"mouse_follow": False, "notifications": True}
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(1)}

        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.get_current_host") as mock_gch,
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertIsNone(state["pending_host"])
        mock_gch.assert_not_called()

    def test_mouse_follow_enabled_sends_normally(self):
        """mouse_follow=True → comportement normal (CHANGE_HOST envoyé)."""
        _mock_gui.prefs = {"mouse_follow": True, "notifications": True}
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical", "last_change_host_at": time.time()}

        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        mock_send.assert_called_once()

    def test_mouse_follow_default_true(self):
        """Préférence absente → mouse_follow est True par défaut."""
        _mock_gui.prefs = {"notifications": True}  # pas de mouse_follow
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical", "last_change_host_at": time.time()}

        with (
            patch("swigi.daemon.prefs", _mock_gui.prefs),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        mock_send.assert_called_once()


# ── Connexions fantômes BT ────────────────────────────────────────────────────


class TestPhantomReconnection(unittest.TestCase):
    """Tests pour la détection des connexions fantômes BT.

    Un clavier en transit vers un autre Mac peut apparaître brièvement connectable.
    Si disconnect < _KEYBOARD_PHANTOM_WINDOW après reconnexion (sans switch) → pending_host effacé.
    """

    def _run_phantom_scenario(
        self,
        kb_new_reads=None,
        initial_pending_host=1,
        expect_cleared=True,
        timeout=4.0,
    ):
        """
        Simule : old_keyboard → disconnect → reconnect (new_keyboard) → disconnect immédiat.
        Retourne (state final, event_queueueue).
        """
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        old_keyboard = _make_keyboard(name="MX Keys S")
        old_keyboard.product_id = 0xB35B
        write_calls_old = [0]

        def write_old(msg):
            write_calls_old[0] += 1
            if write_calls_old[0] >= 2:
                raise TransportError("disconnect")

        old_keyboard.transport.write.side_effect = write_old
        old_keyboard.transport.read.side_effect = lambda *a, **kw: None

        new_keyboard = _make_keyboard(name="MX Keys S")
        new_keyboard.product_id = 0xB35B
        write_calls_new = [0]

        def write_new(msg):
            write_calls_new[0] += 1
            # stability ping (call 1) OK, iter suivant (call 2+) → phantom disconnect
            if write_calls_new[0] >= 2:
                raise TransportError("phantom disconnect")

        new_keyboard.transport.write.side_effect = write_new

        if kb_new_reads is not None:
            reads_iter = iter(kb_new_reads)
            new_keyboard.transport.read.side_effect = lambda *a, **kw: next(
                reads_iter, None
            )
        else:
            new_keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {
                old_keyboard.product_id: {"name": old_keyboard.name, "ok": True}
            },
            "pending_host": (initial_pending_host, time.time() + 60),
        }

        with (
            _fast_probe(),
            patch("swigi.daemon._KEYBOARD_STABILITY_SECONDS", 0.0),
            patch(
                "swigi.daemon._find_keyboard_by_product_id", return_value=new_keyboard
            ),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(old_keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            deadline = time.time() + 1.5
            while time.time() < deadline:
                ph = state.get("pending_host")
                if expect_cleared and ph is None:
                    break
                if not expect_cleared and ph is not None and ph[0] == 0:
                    # resync a eu lieu (new_keyboard sur hôte 0) → phantom sans effacement
                    break
                time.sleep(0.02)
            stop.set()
            t.join(timeout=1.5)

        return state, event_queue

    def test_phantom_clears_pending_host(self):
        """Disconnect < _KEYBOARD_PHANTOM_WINDOW après reconnexion → pending_host effacé."""
        state, _ = self._run_phantom_scenario(expect_cleared=True)
        self.assertIsNone(
            state.get("pending_host"),
            "pending_host doit être effacé après détection connexion fantôme",
        )

    def test_switch_event_prevents_phantom_clear(self):
        """Switch détecté pendant disconnect fantôme → pending_host NON effacé (vrai switch)."""
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        switch_msg = bytearray(MSG_LONG_LEN)
        switch_msg[0] = REPORT_LONG
        switch_msg[2] = 5  # change_host_index
        switch_msg[3] = 0x00
        switch_msg[4] = 3
        switch_msg[5] = 1  # target_host = 1
        switch_msg = bytes(switch_msg)

        state, event_queue = self._run_phantom_scenario(
            kb_new_reads=[switch_msg],
            expect_cleared=False,
        )

        # SwitchEvent doit avoir été émis
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        from swigi.daemon import _SwitchEvent

        switch_events = [error for error in events if isinstance(error, _SwitchEvent)]
        if switch_events:
            self.assertEqual(switch_events[0].target_host, 1)

        # pending_host NON effacé (resync l'a recalé sur hôte 0, pas None)
        self.assertIsNotNone(
            state.get("pending_host"),
            "pending_host ne doit PAS être effacé si un switch a été détecté",
        )

    def test_no_phantom_on_first_disconnect(self):
        """Premier disconnect sans reconnect précédent → pending_host conservé."""
        import queue as q_module

        event_queue = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        keyboard = _make_keyboard(name="MX Keys S")
        keyboard.product_id = 0xB35B
        write_calls = [0]

        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("disconnect")

        keyboard.transport.write.side_effect = write_side
        keyboard.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "keyboards": {keyboard.product_id: {"name": keyboard.name, "ok": True}},
            "pending_host": (1, time.time() + 60),
        }

        with (
            _fast_probe(),
            patch("swigi.daemon._KEYBOARD_STABILITY_SECONDS", 0.0),
            patch("swigi.daemon._find_keyboard_by_product_id", return_value=None),
            patch("swigi.daemon.notify"),
        ):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(keyboard, event_queue, state, stop, hunt),
                daemon=True,
            )
            t.start()
            time.sleep(0.1)
            stop.set()
            t.join(timeout=1.5)

        # Pas de reconnect précédent → phantom check non déclenché → pending conservé
        ph = state.get("pending_host")
        self.assertIsNotNone(ph)
        self.assertEqual(
            ph[0], 1, "pending_host ne doit PAS être effacé au premier disconnect"
        )

    def test_phantom_in_complex_abc_scenario(self):
        """Scénario A→B→A : connexion fantôme au retour → souris ne va pas sur mauvais Mac."""
        keyboard = _make_keyboard(name="MX Keys S")
        state = {"pending_host": (1, time.time() + 60)}  # stale post-switch A→B

        # 1. Clavier revient sur A (hôte 0) — resync
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(keyboard, state)

        # pending_host recalé sur hôte 0
        self.assertEqual(state["pending_host"][0], 0)

        # 2. Connexion fantôme détectée (simule effacement par _watch_keyboard)
        state["pending_host"] = None  # ce que fait le phantom check

        # 3. Souris revient — pas de pending_host → aucune correction envoyée
        mouse = _make_mouse()
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(
            result, "Aucune correction attendue — pending_host effacé par fantôme"
        )
        mock_send.assert_not_called()


class TestComplexSwitchSequences(unittest.TestCase):
    """Tests scénarios A→B→A→B→C→B→C→A→B→C→B→A — logique pure, sans threads."""

    def setUp(self):
        self.mouse = _make_mouse(name="MX Master 4")
        self.keyboard = _make_keyboard(name="MX Keys S")
        self.state = {
            "pending_host": None,
            "mouse": "MX Master 4",
            "mice": ["MX Master 4"],
        }
        self.lock = threading.Lock()
        self.sent = []

    def _send(self, target):
        """Simule switch clavier → envoie CHANGE_HOST à la souris."""
        mice = [self.mouse] if self.mouse.transport.is_open else []
        with patch(
            "swigi.daemon.send_change_host",
            side_effect=lambda *a: self.sent.append(a[3]),
        ):
            _send_to_all_mice(mice, target, self.state, self.lock)
        # Réouvrir le mock transport pour simulations suivantes
        self.mouse.transport.is_open = True

    def _kb_return(self, host):
        """Simule le retour du clavier sur un hôte — resync pending_host."""
        with patch("swigi.daemon.get_current_host", return_value=host):
            _resync_pending_host_from_keyboard(self.keyboard, self.state)

    def _mouse_check(self, current_host):
        """Simule la vérification de sync souris — retourne (corrected, target_sent)."""
        correction_target = []
        with (
            patch("swigi.daemon.get_current_host", return_value=current_host),
            patch(
                "swigi.daemon.send_change_host",
                side_effect=lambda *a: correction_target.append(a[3]),
            ),
        ):
            result = _check_and_apply_pending_host(self.mouse, self.state)
        self.mouse.transport.is_open = True  # reset mock
        return result, correction_target

    def test_A_B_A_no_spurious_correction(self):
        """A→B→A : souris revenue sur A, pas de correction."""
        self._send(1)  # A→B
        self._kb_return(0)  # clavier revenu sur A
        corrected, targets = self._mouse_check(0)  # souris aussi sur A
        self.assertFalse(corrected)
        self.assertEqual(targets, [])

    def test_A_B_A_desync_corrected(self):
        """A→B→A : souris bloquée sur B → correction vers A."""
        self._send(1)  # A→B
        self._kb_return(0)  # clavier revenu sur A
        corrected, targets = self._mouse_check(1)  # souris encore sur B
        self.assertTrue(corrected)
        self.assertEqual(targets, [0])

    def test_A_B_C_A_no_spurious_correction(self):
        """A→B, B→C, C→A : souris et clavier sur A, pas de correction."""
        self._send(1)  # A→B
        # B→C : Mac A ne voit pas ça (keyboard parti)
        # C→A : keyboard reconnecte
        self._kb_return(0)  # resync : clavier sur A (hôte 0)
        corrected, targets = self._mouse_check(0)  # souris sur A
        self.assertFalse(corrected)
        self.assertEqual(targets, [])

    def test_A_B_C_A_mouse_stuck_on_C(self):
        """A→B→C→A : souris bloquée sur C (hôte 2) → correction vers A."""
        self._send(1)  # A→B
        self._kb_return(0)  # C→A : resync
        corrected, targets = self._mouse_check(2)  # souris encore sur C
        self.assertTrue(corrected)
        self.assertEqual(targets, [0])

    def test_A_B_A_B_no_double_correction(self):
        """A→B→A→B : second switch écrase le premier pending."""
        self._send(1)  # A→B
        self._kb_return(0)  # B→A
        self.assertEqual(self.state["pending_host"][0], 0)
        # On check la sync de la souris (elle est sur A)
        corrected, _ = self._mouse_check(0)
        self.assertFalse(corrected)
        self.assertIsNone(self.state["pending_host"])

        # Re-switch A→B
        self._send(1)  # A→B again
        self.assertEqual(self.state["pending_host"][0], 1)

    def test_A_B_C_B_A_complex(self):
        """A→B→C→B→A : pending_host recalé à chaque retour sur A."""
        # Étape 1 : A→B
        self._send(1)
        self.assertEqual(self.state["pending_host"][0], 1)

        # Étape 2 : clavier revient sur A (depuis B, puis C, puis B... final A)
        # Mac A ne voit que le retour final
        self._kb_return(0)
        self.assertEqual(self.state["pending_host"][0], 0)

        # Étape 3 : souris revenue sur A — pas de correction
        corrected, targets = self._mouse_check(0)
        self.assertFalse(corrected)
        self.assertIsNone(self.state["pending_host"])

    def test_full_sequence_A_B_A_B_C_B_C_A_B_C_B_A(self):
        """Scénario complet 12 étapes — vérifie pending_host à chaque retour sur A."""
        seq = [
            ("switch", 1),  # A→B
            ("return_kb", 0),  # B→A
            ("check_ok", 0),  # souris sur A : sync
            ("switch", 1),  # A→B
            # B→C→B→C : Mac A ne voit pas
            ("return_kb", 0),  # C/B→A (depuis C)
            ("check_ok", 0),  # souris sur A : sync
            ("switch", 1),  # A→B
            # B→C→B : Mac A ne voit pas
            ("return_kb", 0),  # B→A
            ("check_ok", 0),  # souris sur A : sync
        ]
        corrections = []
        for op, host in seq:
            if op == "switch":
                self._send(host)
            elif op == "return_kb":
                self._kb_return(host)
            elif op == "check_ok":
                with (
                    patch("swigi.daemon.get_current_host", return_value=host),
                    patch(
                        "swigi.daemon.send_change_host",
                        side_effect=lambda *a: corrections.append(a[3]),
                    ),
                ):
                    result = _check_and_apply_pending_host(self.mouse, self.state)
                self.mouse.transport.is_open = True
                self.assertFalse(
                    result, f"Correction inattendue pour op={op} host={host}"
                )

        self.assertEqual(corrections, [], f"Corrections spurieuses : {corrections}")

    def test_desync_after_3_host_cycle(self):
        """Cycle complet avec désync réelle : souris corrigée exactement 1 fois."""
        # A→B
        self._send(1)
        # B→A : clavier revenu
        self._kb_return(0)
        # Souris encore sur B (hôte 1) → correction vers A (hôte 0)
        corrected, targets = self._mouse_check(1)
        self.assertTrue(corrected)
        self.assertEqual(targets, [0])
        # pending_host effacé après correction
        self.assertIsNone(self.state["pending_host"])

    def test_rapid_switch_pending_host_reflects_last_target(self):
        """Switches rapides : le dernier switch gagne."""
        mice = [self.mouse]
        with patch("swigi.daemon.send_change_host", side_effect=lambda *a: None):
            _send_to_all_mice(mice, 1, self.state, self.lock)  # A→B
            _send_to_all_mice(mice, 2, self.state, self.lock)  # B→C immédiatement
        self.assertEqual(
            self.state["pending_host"][0], 2, "Le 2ème switch doit écraser le 1er"
        )

    def test_pending_host_ttl_prevents_stale_correction(self):
        """pending_host expiré → pas de correction même si désync."""
        self.state["pending_host"] = (1, time.time() - 1.0)  # déjà expiré
        corrected, targets = self._mouse_check(0)
        self.assertFalse(corrected)
        self.assertEqual(targets, [])
        self.assertIsNone(self.state["pending_host"])


class TestMouseFollowsDuringMovement(unittest.TestCase):
    """Souris en mouvement (transport ouvert) doit quand même suivre le clavier."""

    def test_mouse_follow_sent_even_when_mouse_transport_open(self):
        """Switch clavier → CHANGE_HOST envoyé même si transport souris still open."""
        mouse = _make_mouse()
        mouse.transport.is_open = True  # souris active/en mouvement
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        lock = threading.Lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice, 1, state, lock)

        mock_send.assert_called_once()
        self.assertEqual(state["pending_host"][0], 1)
        self.assertEqual(mice, [])  # liste vidée après switch

    def test_pending_host_set_after_switch_with_open_mice(self):
        """pending_host correctement mis à jour après switch avec souris ouverte."""
        mouse = _make_mouse()
        mouse.transport.is_open = True
        mice = [mouse]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        lock = threading.Lock()

        with patch("swigi.daemon.send_change_host"):
            _send_to_all_mice(mice, 2, state, lock)

        self.assertIsNotNone(state["pending_host"])
        self.assertEqual(state["pending_host"][0], 2)
        self.assertIsNone(state["mouse"])


# ── Scénario junior : 3 Macs, clavier + souris, switch complet A→B→C ─────────


class TestJuniorThreeMacsEndToEnd(unittest.TestCase):
    """Cas d'usage junior : 3 Macs (hôtes 0,1,2), 1 clavier, 1 souris.
    La souris DOIT suivre le clavier sur chaque switch, même en mouvement.
    """

    def _switch_to(self, mice_list, target, state, lock):
        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice_list, target, state, lock)
        return mock_send

    def test_mouse_follows_keyboard_abc(self):
        """Switch A→B→C : CHANGE_HOST envoyé à la souris pour chaque étape."""
        lock = threading.Lock()
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}

        # Switch A→B (hôte 0→1)
        mouse_ab = _make_mouse()
        mouse_ab.product_id = 0xB025
        mice = [mouse_ab]
        mock_send = self._switch_to(mice, 1, state, lock)
        mock_send.assert_called_once()
        _, _, _, host_sent = mock_send.call_args[0]
        self.assertEqual(host_sent, 1)
        self.assertEqual(state["pending_host"][0], 1)

        # Switch B→C (hôte 1→2)
        mouse_bc = _make_mouse()
        mouse_bc.product_id = 0xB025
        mice[:] = [mouse_bc]
        mock_send2 = self._switch_to(mice, 2, state, lock)
        mock_send2.assert_called_once()
        _, _, _, host_sent2 = mock_send2.call_args[0]
        self.assertEqual(host_sent2, 2)
        self.assertEqual(state["pending_host"][0], 2)

    def test_mouse_follows_keyboard_even_when_transport_open(self):
        """Souris en mouvement (transport ouvert) → CHANGE_HOST quand même envoyé."""
        lock = threading.Lock()
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical"]}
        mouse = _make_mouse()
        mouse.transport.is_open = True  # souris active, en train de bouger
        mice = [mouse]

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice, 1, state, lock)

        mock_send.assert_called_once()
        self.assertEqual(state["pending_host"][0], 1)
        self.assertEqual(mice, [], "mice_list doit être vide après switch")

    def test_pending_host_cleared_after_sync_on_probe(self):
        """probe loop : souris déjà sur le bon hôte → pending_host effacé."""
        mouse = _make_mouse()
        mouse.product_id = 0xB025
        state = {"pending_host": _pending(1), "mouse": None, "mice": []}
        lock = threading.Lock()
        mice_list = []

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[mouse]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=1),
        ):  # déjà sur hôte 1
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.15)
            stop.set()
            t.join(timeout=1)

        self.assertIsNone(
            state["pending_host"],
            "pending_host doit être effacé quand souris déjà sync",
        )

    def test_pending_host_corrected_on_desync(self):
        """probe loop : souris sur mauvais hôte → CHANGE_HOST de correction envoyé."""
        mouse = _make_mouse()
        mouse.product_id = 0xB025
        sent_hosts = []
        state = {"pending_host": _pending(1), "mouse": None, "mice": [], "last_change_host_at": time.time()}
        lock = threading.Lock()
        mice_list = []

        def fake_send(transport, device, feat, target):
            sent_hosts.append(target)

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[mouse]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host", side_effect=fake_send),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.15)
            stop.set()
            t.join(timeout=1)

        self.assertIn(
            1, sent_hosts, "Correction désync → hôte 1 doit avoir été envoyée"
        )

    def test_resync_does_not_overwrite_newer_switch(self):
        """_resync: switch arrivé pendant I/O → pending_host du switch conservé."""
        keyboard = _make_keyboard()
        # Simuler: pendant le resync I/O, un switch arrive et change pending_host
        call_count = [0]
        pending_objects = []

        def fake_get_host(*a, **kw):
            # Simule un switch concurrent pendant le 1er retry
            if call_count[0] == 0:
                new_pending = (
                    2,
                    time.time() + 60,
                )  # nouveau objet tuple → switch détecté
                state["pending_host"] = new_pending
                pending_objects.append(new_pending)
            call_count[0] += 1
            return 0  # clavier sur hôte 0

        state = {"pending_host": _pending(1)}

        with (
            patch("swigi.daemon.get_current_host", side_effect=fake_get_host),
            patch("swigi.daemon._RESYNC_RETRIES", 1),
        ):
            from swigi.daemon import _resync_pending_host_from_keyboard

            _resync_pending_host_from_keyboard(keyboard, state)

        # Le resync ne doit PAS écraser le pending_host mis à jour par le switch
        self.assertIsNotNone(state["pending_host"])
        self.assertEqual(
            state["pending_host"][0], 2, "pending_host du switch doit être conservé"
        )


# ── TestResyncRobustness ──────────────────────────────────────────────────────


class TestResyncRobustness(unittest.TestCase):
    """Tests de robustesse de _resync_pending_host_from_keyboard sur M1/M3."""

    def test_resync_succeeds_on_4th_retry(self):
        """Valide l'augmentation de _RESYNC_RETRIES à 5 : les 3 premiers appels
        get_current_host retournent None, le 4ème retourne 1.
        Vérifie que pending_host est bien mis à (1, ...).
        Bug visé : si _RESYNC_RETRIES était ≤ 3, le 4ème retry n'aurait jamais lieu."""
        keyboard = _make_keyboard()
        state = {"pending_host": None}
        call_results = [None, None, None, 1]
        call_count = [0]

        def fake_gch(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            return call_results[idx] if idx < len(call_results) else None

        with (
            patch("swigi.daemon.get_current_host", side_effect=fake_gch),
            patch("swigi.daemon._RESYNC_RETRIES", 5),
            patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertIsNotNone(
            state["pending_host"],
            "pending_host doit être défini : le 4ème retry a retourné 1",
        )
        self.assertEqual(
            state["pending_host"][0],
            1,
            "pending_host[0] doit être 1 (hôte retourné au 4ème essai)",
        )

    def test_resync_keeps_existing_pending_when_all_retries_fail(self):
        """Révèle le bug potentiel : get_current_host retourne None à TOUS les essais.
        pending_host initial = (2, deadline). Vérifie que pending_host N'EST PAS effacé.
        Comportement attendu (code actuel) : pending_host conservé car existant.
        Ce test échouerait si le code effaçait pending_host sur échec total."""
        keyboard = _make_keyboard()
        state = {"pending_host": _pending(2)}

        with (
            patch("swigi.daemon.get_current_host", return_value=None),
            patch("swigi.daemon._RESYNC_RETRIES", 3),
            patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertIsNotNone(
            state["pending_host"],
            "pending_host doit être CONSERVÉ quand hôte clavier illisible (pas effacé)",
        )
        self.assertEqual(
            state["pending_host"][0],
            2,
            "pending_host doit conserver l'hôte initial (2) quand resync échoue",
        )

    def test_resync_clears_pending_when_no_prior_pending_and_all_fail(self):
        """get_current_host retourne None partout, pending_host initial = None.
        Vérifie que pending_host reste None (comportement OK dans ce cas).
        Pas de régression : un None initial ne doit pas devenir non-None."""
        keyboard = _make_keyboard()
        state = {"pending_host": None}

        with (
            patch("swigi.daemon.get_current_host", return_value=None),
            patch("swigi.daemon._RESYNC_RETRIES", 3),
            patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertIsNone(
            state["pending_host"],
            "pending_host doit rester None quand aucun pending préexistant et resync échoue",
        )

    def test_resync_with_slow_keyboard_eventually_succeeds(self):
        """Simule un clavier M3 lent : les 2 premiers appels get_current_host
        retournent None (délai 0.05s chacun), puis retourne 2.
        Vérifie que pending_host = (2, ...) après resync.
        Bug visé : timeout trop court ou retries insuffisants sur M3."""
        keyboard = _make_keyboard()
        state = {"pending_host": None}
        call_count = [0]

        def slow_gch(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                time.sleep(0.05)
                return None
            return 2

        with (
            patch("swigi.daemon.get_current_host", side_effect=slow_gch),
            patch("swigi.daemon._RESYNC_RETRIES", 5),
            patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertIsNotNone(
            state["pending_host"],
            "pending_host doit être défini : le clavier M3 lent finit par répondre",
        )
        self.assertEqual(
            state["pending_host"][0],
            2,
            "pending_host[0] doit être 2 (hôte retourné après délai)",
        )
        self.assertGreaterEqual(
            call_count[0],
            3,
            "Au moins 3 appels attendus (2 slow-None + 1 succès)",
        )


# ── TestProbeLoopRaceCondition ────────────────────────────────────────────────


class TestProbeLoopRaceCondition(unittest.TestCase):
    """Race condition : _send_to_all_mice ferme le transport avant probe Pass 2."""

    def _make_lock(self):
        return threading.Lock()

    def test_pass2_skips_closed_transport_after_race(self):
        """Simule la race condition : new_mouse.transport.is_open devient False
        entre Pass 1 (sous lock) et Pass 2 (hors lock) car _send_to_all_mice
        a tourné entretemps.
        Vérifie : get_current_host N'EST PAS appelé, aucun crash.
        Bug visé : sans le guard is_open en Pass 2, I/O sur transport mort → crash
        ou comportement indéfini."""
        new_m = _make_mouse(name="MX Master 4")
        new_m.product_id = 0xB042
        # Transport fermé avant que Pass 2 s'exécute (race simulée statiquement)
        new_m.transport.is_open = False

        mice_list = []
        state = {"pending_host": _pending(1), "mouse": None, "mice": []}
        lock = self._make_lock()
        gch_called = []

        def tracking_gch(*args, **kwargs):
            gch_called.append(True)
            return 1

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[new_m]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", side_effect=tracking_gch),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.12)
            stop.set()
            t.join(timeout=1)

        self.assertEqual(
            gch_called,
            [],
            "get_current_host ne doit PAS être appelé si transport fermé (race guard)",
        )

    def test_pass2_processes_normally_when_transport_open(self):
        """Cas nominal : transport ouvert → _check_and_apply_pending_host appelé normalement.
        Vérifie : get_current_host IS appelé et pending_host est traité.
        Régression : le guard is_open ne doit pas bloquer les cas normaux."""
        new_m = _make_mouse(name="MX Master 4")
        new_m.product_id = 0xB042
        new_m.transport.is_open = True

        mice_list = []
        state = {"pending_host": _pending(1), "mouse": None, "mice": []}
        lock = self._make_lock()
        gch_called = threading.Event()

        def tracking_gch(*args, **kwargs):
            gch_called.set()
            return 1  # sync OK → pending_host effacé

        with (
            _fast_probe(),
            patch("swigi.daemon.find_all_devices", return_value=[new_m]),
            patch("swigi.daemon.notify"),
            patch("swigi.daemon.get_current_host", side_effect=tracking_gch),
        ):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            gch_called.wait(timeout=1.0)
            stop.set()
            t.join(timeout=1)

        self.assertTrue(
            gch_called.is_set(),
            "get_current_host doit être appelé quand transport ouvert (Pass 2 normal)",
        )


# ── TestThreeMacsFullFlow ─────────────────────────────────────────────────────


class TestThreeMacsFullFlow(unittest.TestCase):
    """Flux complet 3 Macs : Mac A voit le switch, Mac B reçoit le clavier + cherche la souris."""

    def _make_lock(self):
        return threading.Lock()

    def test_mac_a_sends_mouse_when_switch_to_b(self):
        """Switch A→B avec souris sur Mac A → CHANGE_HOST envoyé vers hôte 1.
        mice_list vidée. pending_host=(1,...).
        Bug visé : CHANGE_HOST non envoyé ou pending_host non mis à jour."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.product_id = 0xB042
        mice_list = [mouse]
        state = {
            "pending_host": None,
            "mouse": "MX Master 4",
            "mice": ["MX Master 4"],
        }
        lock = self._make_lock()
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        with patch("swigi.daemon.send_change_host", side_effect=fake_send):
            _send_to_all_mice(mice_list, 1, state, lock)

        self.assertEqual(sent, [1], "CHANGE_HOST vers hôte 1 doit être envoyé")
        self.assertEqual(mice_list, [], "mice_list doit être vidée après switch")
        self.assertIsNotNone(state["pending_host"], "pending_host doit être défini")
        self.assertEqual(state["pending_host"][0], 1, "pending_host[0] doit être 1")

    def test_mac_b_probe_finds_mouse_sync_ok(self):
        """Mac B : pending_host=(1,...), probe trouve souris sur hôte 1 → sync OK,
        pending_host=None.
        Bug visé : sync non détectée, pending_host non effacé."""
        mouse = _make_mouse(name="MX Master 4")
        state = {"pending_host": _pending(1), "mouse": None}

        with (
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result, "Pas de correction attendue (sync OK)")
        mock_send.assert_not_called()
        self.assertIsNone(
            state["pending_host"], "pending_host doit être effacé après sync"
        )

    def test_mac_b_probe_finds_mouse_desync_corrects(self):
        """Mac B : pending_host=(1,...), probe trouve souris sur hôte 0 → désync.
        CHANGE_HOST de correction vers 1 doit être envoyé.
        Bug visé : désync non détectée sur Mac B, souris reste sur mauvais hôte."""
        mouse = _make_mouse(name="MX Master 4")
        state = {"pending_host": _pending(1), "mouse": None, "last_change_host_at": time.time()}
        correction_sent = []

        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch(
                "swigi.daemon.send_change_host",
                side_effect=lambda t, d, f, h: correction_sent.append(h),
            ),
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result, "Correction attendue (désync)")
        self.assertEqual(correction_sent, [1], "Correction doit être vers hôte 1")

    def test_mac_a_switch_with_empty_mice_list_sets_pending(self):
        """Mac A n'a pas la souris (mice_list=[]) → switch A→B → pending_host=(1,...).
        Cas où la souris était déjà sur Mac B ou C.
        Bug visé : pending_host non défini quand mice_list vide, Mac B ne sait
        pas qu'il doit synchroniser."""
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice_list, 1, state, lock)

        mock_send.assert_not_called()
        self.assertIsNotNone(
            state["pending_host"],
            "pending_host doit être défini même si mice_list vide",
        )
        self.assertEqual(
            state["pending_host"][0],
            1,
            "pending_host[0] doit être 1 (hôte cible du switch)",
        )

    def test_mac_b_resync_after_kb_connect_sets_pending(self):
        """Mac B reçoit clavier (hôte 1), _resync_pending_host_from_keyboard →
        pending_host=(1,...) sur Mac B.
        Bug visé : pending_host non défini sur Mac B après connexion clavier,
        souris non synchronisée."""
        keyboard = _make_keyboard(change_host_index=5, name="MX Keys S")
        state = {"pending_host": None}

        with (
            patch("swigi.daemon.get_current_host", return_value=1),
            patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertIsNotNone(
            state["pending_host"],
            "pending_host doit être défini après resync clavier sur hôte 1",
        )
        self.assertEqual(
            state["pending_host"][0],
            1,
            "pending_host[0] doit être 1 (hôte actuel du clavier)",
        )

    def test_mac_b_resync_fail_then_probe_has_no_pending_but_mouse_sync_anyway(self):
        """Mac B : resync échoue (get_current_host→None partout), pending_host=None.
        Probe trouve souris. Vérifie qu'AUCUNE correction spurieuse n'est envoyée.
        Bug visé : correction envoyée vers hôte aléatoire quand pending_host=None,
        causant un switch non voulu de la souris."""
        mouse = _make_mouse(name="MX Master 4")
        state = {"pending_host": None, "mouse": None}

        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon.send_change_host") as mock_send,
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result, "Pas de correction quand pending_host=None")
        mock_send.assert_not_called()

    def test_mouse_moving_during_switch_still_gets_change_host(self):
        """transport.is_open=True, souris génère des reads dans find_all_devices.
        Lors de _send_to_all_mice, send_change_host est appelé malgré tout.
        Bug visé : CHANGE_HOST bloqué ou sauté si la souris est active."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.transport.is_open = True  # souris en mouvement
        mice_list = [mouse]
        state = {
            "pending_host": None,
            "mouse": "MX Master 4",
            "mice": ["MX Master 4"],
        }
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice_list, 2, state, lock)

        mock_send.assert_called_once()
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 2, "CHANGE_HOST doit cibler hôte 2")
        self.assertEqual(state["pending_host"][0], 2)
        self.assertEqual(mice_list, [], "mice_list vidée après switch")

    def test_abc_cycle_no_spurious_correction_with_resync(self):
        """Cycle complet A→B→C→A avec _resync_pending_host_from_keyboard à chaque retour.
        Vérifie qu'aucune correction spurieuse n'est envoyée.
        Bug visé : resync malformé cause une correction vers mauvais hôte
        lors du cycle 3-Macs."""
        mouse = _make_mouse(name="MX Master 4")
        keyboard = _make_keyboard(name="MX Keys S")
        lock = self._make_lock()
        state = {
            "pending_host": None,
            "mouse": "MX Master 4",
            "mice": ["MX Master 4"],
        }

        # A→B : switch depuis Mac A
        with patch("swigi.daemon.send_change_host"):
            _send_to_all_mice([mouse], 1, state, lock)
        mouse.transport.is_open = True  # reset mock pour la suite

        self.assertEqual(state["pending_host"][0], 1)

        # B→C : Mac A ne voit pas ce switch (clavier parti)
        # C→A : clavier revient sur Mac A (hôte 0) — resync
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch("swigi.daemon._RESYNC_RETRY_DELAY", 0.0),
        ):
            _resync_pending_host_from_keyboard(keyboard, state)

        self.assertEqual(
            state["pending_host"][0],
            0,
            "pending_host doit être recalé sur hôte 0 après retour clavier",
        )

        # Vérification souris sur A (hôte 0) → pas de correction
        corrections = []
        with (
            patch("swigi.daemon.get_current_host", return_value=0),
            patch(
                "swigi.daemon.send_change_host",
                side_effect=lambda t, d, f, h: corrections.append(h),
            ),
        ):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result, "Aucune correction attendue après cycle A→B→C→A sync")
        self.assertEqual(
            corrections,
            [],
            f"Corrections spurieuses détectées : {corrections}",
        )
        self.assertIsNone(state["pending_host"])


# ── TestGetCurrentHostWithTimeout (protocol) ──────────────────────────────────


class TestGetCurrentHostWithTimeout(unittest.TestCase):
    """Tests get_current_host avec paramètre timeout explicite."""

    def _make_valid_response(self, feature_index, num_hosts=3, current_host=1):
        import struct

        from swigi.constants import REPORT_LONG, SW_ID

        request_id = (feature_index << 8) | 0x00 | SW_ID
        response = (
            struct.pack("!BB", REPORT_LONG, 0xFF)
            + struct.pack("!H", request_id)
            + bytes([num_hosts, current_host])
            + b"\x00" * 14
        )
        return response

    def _make_transport(self, responses=None):
        """Transport minimal avec file de réponses."""

        class _Transport:
            def __init__(self, resps):
                self._resps = list(resps or [])
                self.is_open = True

            def write(self, msg):
                pass

            def read(self, timeout=500):
                return self._resps.pop(0) if self._resps else None

            def close(self):
                self.is_open = False

        return _Transport(responses or [])

    def test_get_current_host_accepts_timeout_param(self):
        """Appeler get_current_host(transport, 0xFF, 0x09, timeout=1000) ne doit pas
        lever d'erreur et doit retourner l'hôte actuel.
        Régression : si timeout n'est pas accepté comme kwarg, TypeError est levée."""
        from swigi.protocol import get_current_host

        feature_index = 0x09
        transport = self._make_transport([self._make_valid_response(feature_index)])

        result = get_current_host(transport, 0xFF, feature_index, timeout=1000)

        self.assertEqual(
            result,
            1,
            "get_current_host avec timeout=1000 doit retourner l'hôte actuel (1)",
        )

    def test_get_current_host_default_timeout_still_works(self):
        """Sans paramètre timeout → fonctionnement normal inchangé.
        Régression : ajout du paramètre timeout ne doit pas casser le comportement
        par défaut (retourne None si pas de réponse)."""
        from swigi.protocol import get_current_host

        transport = self._make_transport()  # pas de réponse → None attendu

        result = get_current_host(transport, 0xFF, 0x09)

        self.assertIsNone(
            result,
            "get_current_host sans timeout doit retourner None quand pas de réponse",
        )


if __name__ == "__main__":
    unittest.main()
