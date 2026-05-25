import sys
import time
import unittest
from unittest.mock import MagicMock, call, patch

# Mock hidapi_loader + gui AVANT tout import swigi (effets de bord)
_mock_loader = MagicMock()
_mock_loader.lib = MagicMock()
_mock_loader.hid_err = MagicMock(return_value="mock error")
_mock_loader.DeviceInfoStruct = MagicMock()
sys.modules.setdefault("swigi.hidapi_loader", _mock_loader)

_mock_gui = MagicMock()
_mock_gui.notify = MagicMock()
_mock_gui.prefs = {}
_mock_gui.HAS_RUMPS = False
_mock_gui.SwiGiMenuBar = None
sys.modules.setdefault("swigi.gui", _mock_gui)

from swigi.daemon import (  # noqa: E402
    _apply_bm_profile_if_needed,
    _check_and_apply_pending_host,
    _resync_pending_host_from_keyboard,
)
from swigi.transport import TransportError  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_mouse(change_host_idx=9, name="MX Vertical"):
    mouse = MagicMock()
    mouse.name = name
    mouse.change_host_idx = change_host_idx
    mouse.transport.is_open = True
    return mouse


def _make_kb(change_host_idx=5, name="MX Keys S"):
    kb = MagicMock()
    kb.name = name
    kb.change_host_idx = change_host_idx
    kb.transport.is_open = True
    return kb


def _pending(host, ttl=60.0):
    return (host, time.time() + ttl)


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
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send:
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        mock_send.assert_called_once()
        mouse.close.assert_called_once()
        self.assertIsNone(state["mouse"])
        self.assertIsNone(state["pending_host"])

    def test_desync_correction_fails_keeps_pending_closes_mouse(self):
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host", side_effect=TransportError("dead")):
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
        state = {"pending_host": _pending(2), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=1), \
             patch("swigi.daemon.send_change_host") as mock_send:
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 2)

    def test_3hosts_desync_host2_expected_host0(self):
        """Retour hôte 2→0 avec souris bloquée sur hôte 2 — correction vers 0."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(0), "mouse": "MX Vertical"}
        with patch("swigi.daemon.get_current_host", return_value=2), \
             patch("swigi.daemon.send_change_host") as mock_send:
            self.assertTrue(_check_and_apply_pending_host(mouse, state))
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 0)


# ── _resync_pending_host_from_keyboard ─────────────────────────────────────────

class TestResyncPendingHostFromKeyboard(unittest.TestCase):
    """Tests du fix principal : resync pending_host après reconnexion clavier."""

    def test_sets_pending_host_to_keyboard_current_host(self):
        kb = _make_kb()
        state = {"pending_host": None}
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)
        self.assertIsNotNone(state["pending_host"])
        self.assertEqual(state["pending_host"][0], 0)

    def test_clears_pending_host_when_kb_unreadable(self):
        kb = _make_kb()
        state = {"pending_host": _pending(1)}
        with patch("swigi.daemon.get_current_host", return_value=None):
            _resync_pending_host_from_keyboard(kb, state)
        self.assertIsNone(state["pending_host"])

    def test_overwrites_stale_pending_host(self):
        """Bug principal : pending_host stale (hôte 1) remplacé par hôte réel (0)."""
        kb = _make_kb()
        state = {"pending_host": _pending(1)}  # stale depuis switch précédent
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)
        self.assertEqual(state["pending_host"][0], 0)

    def test_3hosts_stale_host2_kb_returns_to_host0(self):
        """3 hôtes : pending stale=2, clavier revient sur 0 — recalé à 0."""
        kb = _make_kb()
        state = {"pending_host": _pending(2)}
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)
        self.assertEqual(state["pending_host"][0], 0)

    def test_3hosts_kb_reconnects_on_host1(self):
        """Clavier revient sur hôte 1 (2e Mac) — pending_host pointe vers 1."""
        kb = _make_kb()
        state = {"pending_host": None}
        with patch("swigi.daemon.get_current_host", return_value=1):
            _resync_pending_host_from_keyboard(kb, state)
        self.assertEqual(state["pending_host"][0], 1)

    def test_pending_ttl_is_refreshed(self):
        """TTL pending_host recalculé à la reconnexion clavier."""
        kb = _make_kb()
        old_deadline = time.time() + 1.0  # expire dans 1s
        state = {"pending_host": (0, old_deadline)}
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)
        new_deadline = state["pending_host"][1]
        self.assertGreater(new_deadline, old_deadline)

    def test_second_keyboard_same_host(self):
        """2e clavier avec change_host_idx différent — resync avec son propre idx."""
        kb2 = _make_kb(change_host_idx=7, name="MX Keys S 2")
        state = {"pending_host": _pending(2)}
        with patch("swigi.daemon.get_current_host", return_value=0) as mock_gch:
            _resync_pending_host_from_keyboard(kb2, state)
        # Vérifie que get_current_host est appelé avec le bon change_host_idx
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
        kb = _make_kb()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}  # stale de A→B

        # Clavier revient sur hôte 0 (Mac = A)
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)

        # pending_host recalé sur 0
        self.assertEqual(state["pending_host"][0], 0)

        # Souris aussi revenue sur hôte 0 — pas de correction
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send:
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        mouse.close.assert_not_called()

    def test_3hosts_cycle_no_spurious_correction(self):
        """
        3 hôtes : A(0)→B(1)→C(2)→A(0) — souris ne doit pas bouger au retour sur A.
        """
        mouse = _make_mouse()
        kb = _make_kb()
        state = {"pending_host": _pending(2), "mouse": "MX Vertical"}  # stale de B→C

        # Clavier revient sur hôte 0 (A)
        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)

        self.assertEqual(state["pending_host"][0], 0)

        # Souris aussi sur 0 — pas de correction
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send:
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()

    def test_desync_still_corrected_after_resync(self):
        """
        Après resync, vraie désync (souris sur mauvais hôte) → correction appliquée.
        """
        mouse = _make_mouse()
        kb = _make_kb()
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}  # stale

        with patch("swigi.daemon.get_current_host", return_value=0):
            _resync_pending_host_from_keyboard(kb, state)  # pending_host → 0

        # Souris sur hôte 1 (désync réelle) → correction vers 0
        with patch("swigi.daemon.get_current_host", return_value=1), \
             patch("swigi.daemon.send_change_host") as mock_send:
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        _, _, _, target = mock_send.call_args[0]
        self.assertEqual(target, 0)

    def test_two_keyboards_independent_resync(self):
        """2 claviers : chacun resync pending_host via son propre change_host_idx."""
        state = {"pending_host": _pending(2)}

        kb1 = _make_kb(change_host_idx=5, name="MX Keys S")
        kb2 = _make_kb(change_host_idx=7, name="MX Keys Mini")

        captured_idx = []

        def fake_gch(transport, devnum, feat_idx):
            captured_idx.append(feat_idx)
            return 0

        with patch("swigi.daemon.get_current_host", side_effect=fake_gch):
            _resync_pending_host_from_keyboard(kb1, state)
            _resync_pending_host_from_keyboard(kb2, state)

        self.assertEqual(captured_idx, [5, 7])
        self.assertEqual(state["pending_host"][0], 0)

    def test_two_mice_pending_host_applies_to_active_mouse(self):
        """2 souris présentes : _check_and_apply_pending_host corrige la souris active."""
        mouse1 = _make_mouse(change_host_idx=9, name="MX Vertical")
        mouse2 = _make_mouse(change_host_idx=11, name="MX Master 4")
        state = {"pending_host": _pending(1), "mouse": "MX Vertical"}

        # mouse1 sur hôte 0, attendue sur 1 → correction
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send:
            _check_and_apply_pending_host(mouse1, state)

        _, _, feat_idx, target = mock_send.call_args[0]
        self.assertEqual(feat_idx, 9)   # change_host_idx de mouse1
        self.assertEqual(target, 1)

        # mouse2 non touchée
        mock_send.reset_mock()
        state2 = {"pending_host": _pending(1), "mouse": "MX Master 4"}
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send2:
            _check_and_apply_pending_host(mouse2, state2)

        _, _, feat_idx2, _ = mock_send2.call_args[0]
        self.assertEqual(feat_idx2, 11)  # change_host_idx de mouse2


# ── _apply_bm_profile_if_needed ────────────────────────────────────────────────

class TestApplyBmProfileIfNeeded(unittest.TestCase):

    def test_no_op_when_bm_auto_apply_false(self):
        _mock_gui.prefs = {"bm_auto_apply": False, "bm_profile": "mon-profil"}
        with patch("swigi.daemon.prefs", _mock_gui.prefs), \
             patch("swigi.daemon.SYSTEM", "Darwin"):
            with patch("swigi.bettermouse.apply_profile") as mock_ap:
                _apply_bm_profile_if_needed("MX Vertical")
        mock_ap.assert_not_called()

    def test_no_op_when_bm_profile_missing(self):
        _mock_gui.prefs = {"bm_auto_apply": True}
        with patch("swigi.daemon.prefs", _mock_gui.prefs), \
             patch("swigi.daemon.SYSTEM", "Darwin"):
            with patch("swigi.bettermouse.apply_profile") as mock_ap:
                _apply_bm_profile_if_needed("MX Vertical")
        mock_ap.assert_not_called()

    def test_no_op_on_non_darwin(self):
        _mock_gui.prefs = {"bm_auto_apply": True, "bm_profile": "mon-profil"}
        with patch("swigi.daemon.prefs", _mock_gui.prefs), \
             patch("swigi.daemon.SYSTEM", "Windows"):
            with patch("swigi.bettermouse.apply_profile") as mock_ap:
                _apply_bm_profile_if_needed("MX Vertical")
        mock_ap.assert_not_called()

    def test_calls_apply_profile_when_configured(self):
        _mock_gui.prefs = {"bm_auto_apply": True, "bm_profile": "mon-profil"}
        with patch("swigi.daemon.prefs", _mock_gui.prefs), \
             patch("swigi.daemon.SYSTEM", "Darwin"), \
             patch("swigi.daemon.notify"), \
             patch("swigi.bettermouse.apply_profile") as mock_ap:
            _apply_bm_profile_if_needed("MX Vertical")
        mock_ap.assert_called_once_with("mon-profil", mouse_name="MX Vertical")

    def test_swallows_value_error_profile_mismatch(self):
        _mock_gui.prefs = {"bm_auto_apply": True, "bm_profile": "profil-mx"}
        with patch("swigi.daemon.prefs", _mock_gui.prefs), \
             patch("swigi.daemon.SYSTEM", "Darwin"), \
             patch("swigi.daemon.notify"), \
             patch("swigi.bettermouse.apply_profile", side_effect=ValueError("mauvaise souris")):
            _apply_bm_profile_if_needed("MX Anywhere")  # ne doit pas lever

    def test_swallows_unexpected_exception(self):
        _mock_gui.prefs = {"bm_auto_apply": True, "bm_profile": "profil-mx"}
        with patch("swigi.daemon.prefs", _mock_gui.prefs), \
             patch("swigi.daemon.SYSTEM", "Darwin"), \
             patch("swigi.daemon.notify"), \
             patch("swigi.bettermouse.apply_profile", side_effect=RuntimeError("boom")):
            _apply_bm_profile_if_needed("MX Vertical")  # ne doit pas lever


if __name__ == "__main__":
    unittest.main()
