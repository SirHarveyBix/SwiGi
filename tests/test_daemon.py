import sys
import threading
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
    _find_kb_by_pid,
    _mice_probe_loop,
    _send_to_all_mice,
    _watch_keyboard,
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


# ── _find_kb_by_pid ────────────────────────────────────────────────────────────

class TestFindKbByPid(unittest.TestCase):

    def test_returns_kb_with_matching_pid(self):
        """find_kb_by_pid retourne le clavier dont le PID correspond."""
        kb1 = _make_kb(name="MX Keys S")
        kb1.pid = 0xB35B
        kb2 = _make_kb(name="MX Keys Mini")
        kb2.pid = 0xB361

        with patch("swigi.daemon.find_all_devices", return_value=[kb1, kb2]):
            result = _find_kb_by_pid(0xB35B)

        self.assertIsNotNone(result)
        self.assertEqual(result.pid, 0xB35B)
        self.assertEqual(result.name, "MX Keys S")

    def test_closes_non_matching_candidates(self):
        """Les claviers dont le PID ne correspond pas doivent être fermés."""
        kb1 = _make_kb(name="MX Keys S")
        kb1.pid = 0xB35B
        kb2 = _make_kb(name="MX Keys Mini")
        kb2.pid = 0xB361

        with patch("swigi.daemon.find_all_devices", return_value=[kb1, kb2]):
            result = _find_kb_by_pid(0xB35B)

        # kb2 ne correspond pas, doit être fermé
        kb2.close.assert_called_once()
        # kb1 correspond, ne doit pas être fermé par find_kb_by_pid
        kb1.close.assert_not_called()

    def test_returns_none_when_pid_not_found(self):
        """Retourne None si aucun clavier avec ce PID n'est disponible."""
        kb1 = _make_kb(name="MX Keys S")
        kb1.pid = 0xB35B

        with patch("swigi.daemon.find_all_devices", return_value=[kb1]):
            result = _find_kb_by_pid(0xDEAD)

        self.assertIsNone(result)
        kb1.close.assert_called_once()

    def test_returns_none_when_no_keyboards(self):
        """Retourne None si find_all_devices retourne une liste vide."""
        with patch("swigi.daemon.find_all_devices", return_value=[]):
            result = _find_kb_by_pid(0xB35B)

        self.assertIsNone(result)


# ── _send_to_all_mice ──────────────────────────────────────────────────────────

class TestSendToAllMice(unittest.TestCase):

    def _make_lock(self):
        import threading
        return threading.Lock()

    def test_sends_to_single_mouse(self):
        """Avec une seule souris, envoie CHANGE_HOST et ferme le transport."""
        mouse = _make_mouse(change_host_idx=9, name="MX Vertical")
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
        mouse1 = _make_mouse(change_host_idx=9, name="MX Vertical")
        mouse2 = _make_mouse(change_host_idx=11, name="MX Master 4")
        mice = [mouse1, mouse2]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical", "MX Master 4"]}
        lock = self._make_lock()

        with patch("swigi.daemon.send_change_host") as mock_send:
            _send_to_all_mice(mice, 2, state, lock)

        self.assertEqual(mock_send.call_count, 2)
        mouse1.close.assert_called_once()
        mouse2.close.assert_called_once()

    def test_skips_mouse_with_closed_transport(self):
        """Souris avec transport fermé : skippée, pas d'envoi CHANGE_HOST."""
        mouse_open = _make_mouse(change_host_idx=9, name="MX Vertical")
        mouse_closed = _make_mouse(change_host_idx=11, name="MX Master 4")
        mouse_closed.transport.is_open = False

        mice = [mouse_open, mouse_closed]
        state = {"pending_host": None, "mouse": "MX Vertical", "mice": ["MX Vertical", "MX Master 4"]}
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

        event_q = q_module.Queue()
        stop_event = threading.Event()
        hunt_trigger = threading.Event()

        kb1 = _make_kb(change_host_idx=5, name="MX Keys S")
        kb1.pid = 0xB35B
        kb2 = _make_kb(change_host_idx=7, name="MX Keys Mini")
        kb2.pid = 0xB361

        state = {
            "kbs": {
                kb1.pid: {"name": kb1.name, "ok": True},
                kb2.pid: {"name": kb2.name, "ok": True},
            },
            "pending_host": None,
        }

        # Simuler un événement CHANGE_HOST : construire un message HID++ long
        import struct
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        def _make_switch_msg(change_host_idx, target_host):
            """Construit un message HID++ REPORT_LONG simulant un CHANGE_HOST."""
            msg = bytearray(MSG_LONG_LEN)
            msg[0] = REPORT_LONG
            msg[2] = change_host_idx  # feature index
            msg[3] = 0x00              # sw_id = 0 (notification)
            msg[5] = target_host
            return bytes(msg)

        # Configurer kb1 : ping OK, puis retourne un event switch hôte 1
        switch_msg1 = _make_switch_msg(5, 1)
        _reads1 = iter([switch_msg1])
        kb1.transport.read.side_effect = lambda *a, **kw: next(_reads1, None)
        kb1.transport.write.return_value = None
        kb1.transport.is_open = True

        # Configurer kb2 : ping OK, puis retourne un event switch hôte 2
        switch_msg2 = _make_switch_msg(7, 2)
        _reads2 = iter([switch_msg2])
        kb2.transport.read.side_effect = lambda *a, **kw: next(_reads2, None)
        kb2.transport.write.return_value = None
        kb2.transport.is_open = True

        from swigi.daemon import _watch_keyboard

        # Lancer les deux threads de surveillance
        t1 = threading.Thread(
            target=_watch_keyboard,
            args=(kb1, event_q, state, stop_event, hunt_trigger),
            daemon=True,
        )
        t2 = threading.Thread(
            target=_watch_keyboard,
            args=(kb2, event_q, state, stop_event, hunt_trigger),
            daemon=True,
        )
        t1.start()
        t2.start()

        # Attendre que les events arrivent (timeout 2s)
        events = []
        deadline = time.time() + 2.0
        while len(events) < 2 and time.time() < deadline:
            try:
                ev = event_q.get(timeout=0.1)
                events.append(ev)
            except q_module.Empty:
                pass

        stop_event.set()
        t1.join(timeout=2)
        t2.join(timeout=2)

        # Vérifier qu'on a bien reçu 2 SwitchEvents indépendants
        switch_events = [e for e in events if isinstance(e, _SwitchEvent)]
        self.assertEqual(len(switch_events), 2)

        hosts = {e.target_host for e in switch_events}
        self.assertIn(1, hosts)
        self.assertIn(2, hosts)

        names = {e.kb_name for e in switch_events}
        self.assertIn("MX Keys S", names)
        self.assertIn("MX Keys Mini", names)


class TestMiceProbeLoop(unittest.TestCase):
    """Tests pour _mice_probe_loop : détection, remplacement, retrait, pending_host."""

    def _make_lock(self):
        import threading
        return threading.Lock()

    def test_new_mouse_added_to_list(self):
        """Nouvelle souris détectée → ajoutée à mice_list."""
        new_m = _make_mouse(name="MX Master 4")
        new_m.pid = 0xB042
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with patch("swigi.daemon.find_all_devices", return_value=[new_m]), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=None):
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
            time.sleep(0.3)
            stop.set()
            t.join(timeout=2)

        self.assertGreater(len(mice_list), 0)

    def test_dead_mouse_replaced(self):
        """Souris avec transport fermé → remplacée par nouvelle instance."""
        old_m = _make_mouse(name="MX Master 4")
        old_m.pid = 0xB042
        old_m.transport.is_open = False

        new_m = _make_mouse(name="MX Master 4")
        new_m.pid = 0xB042
        new_m.transport.is_open = True

        mice_list = [old_m]
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with patch("swigi.daemon.find_all_devices", return_value=[new_m]), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=None):
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
            time.sleep(0.3)
            stop.set()
            t.join(timeout=2)

        # new_m doit être dans la liste, pas old_m
        self.assertIn(new_m, mice_list)
        self.assertNotIn(old_m, mice_list)

    def test_dead_mouse_removed_when_not_found(self):
        """Souris morte non retrouvée par find_all_devices → retirée."""
        dead_m = _make_mouse(name="MX Master 4")
        dead_m.pid = 0xB042
        dead_m.transport.is_open = False

        mice_list = [dead_m]
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        with patch("swigi.daemon.find_all_devices", return_value=[]), \
             patch("swigi.daemon.notify"):
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
            time.sleep(0.3)
            stop.set()
            t.join(timeout=2)

        self.assertNotIn(dead_m, mice_list)

    def test_pending_host_applied_at_reconnect(self):
        """pending_host appliqué quand nouvelle souris connectée."""
        new_m = _make_mouse(name="MX Master 4")
        new_m.pid = 0xB042

        mice_list = []
        state = {"pending_host": (1, time.time() + 60), "mouse": None, "mice": []}
        lock = self._make_lock()

        with patch("swigi.daemon.find_all_devices", return_value=[new_m]), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=1):
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
            time.sleep(0.3)
            stop.set()
            t.join(timeout=2)

        # pending_host effacé car sync OK
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

        with patch("swigi.daemon.get_current_host", side_effect=slow_get_host), \
             patch("swigi.daemon.send_change_host") as mock_send:
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

        with patch("swigi.daemon.get_current_host", side_effect=slow_get_host), \
             patch("swigi.daemon.send_change_host") as mock_send:
            result = _check_and_apply_pending_host(mouse, state)

        self.assertFalse(result)
        mock_send.assert_not_called()
        # Le nouveau pending (hôte 2) est préservé
        self.assertEqual(state["pending_host"][0], 2)

    def test_pending_host_same_target_during_io_correction_fires(self):
        """Même cible pendant I/O → correction normale appliquée."""
        mouse = _make_mouse()
        state = {"pending_host": _pending(1), "mouse": "MX Master 4"}

        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send:
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

        with patch("swigi.daemon.get_current_host", side_effect=slow_get_host), \
             patch("swigi.daemon.send_change_host") as mock_send:
            _check_and_apply_pending_host(mouse, state)

        mock_send.assert_not_called()
        self.assertEqual(state["pending_host"][0], 2)


# ── Fix #2 : num_hosts dynamique dans _watch_keyboard ────────────────────────

class TestWatchKeyboardNotificationParsing(unittest.TestCase):
    """Vérifie le parsing de la notification CHANGE_HOST avec num_hosts depuis raw[4]."""

    def _run_watch_and_collect(self, switch_msg):
        """Lance _watch_keyboard, lui soumet un message, retourne l'event reçu."""
        from swigi.daemon import _SwitchEvent
        event_q = __import__("queue").Queue()
        stop = threading.Event()
        hunt = threading.Event()
        kb = _make_kb(change_host_idx=5, name="MX Keys S")
        kb.pid = 0xB35B
        state = {"kbs": {kb.pid: {"name": kb.name, "ok": True}}, "pending_host": None}
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN

        # Retourner le message une fois, puis None indéfiniment (évite StopIteration dans thread)
        _reads = iter([switch_msg])
        kb.transport.read.side_effect = lambda *a, **kw: next(_reads, None)
        kb.transport.write.return_value = None
        kb.transport.is_open = True

        t = threading.Thread(
            target=_watch_keyboard,
            args=(kb, event_q, state, stop, hunt),
            daemon=True,
        )
        t.start()
        events = []
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                events.append(event_q.get(timeout=0.1))
                break
            except __import__("queue").Empty:
                pass
        stop.set()
        t.join(timeout=2)
        return [e for e in events if isinstance(e, _SwitchEvent)]

    def _make_notif(self, change_host_idx, num_hosts, target_host):
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN
        msg = bytearray(MSG_LONG_LEN)
        msg[0] = REPORT_LONG
        msg[2] = change_host_idx
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


# ── Fix #3 : hunt interval réel 1s (pas 6s) ──────────────────────────────────

class TestHuntIntervalTiming(unittest.TestCase):
    """Vérifie que _mice_probe_loop probe toutes les ~1s en hunt mode, pas 6s."""

    def test_hunt_mode_probes_at_1s_not_6s(self):
        """En hunt mode, ≥3 probes doivent se produire en 3.5s."""
        call_times = []

        def fake_find(*args):
            call_times.append(time.time())
            return []

        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = threading.Lock()
        stop = threading.Event()
        hunt = threading.Event()
        hunt.set()

        with patch("swigi.daemon.find_all_devices", side_effect=fake_find), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(3.5)
            stop.set()
            t.join(timeout=2)

        # Avec le bug (6s), on aurait 0-1 probe en 3.5s.
        # Avec le fix (1s), on attend ≥3 probes.
        self.assertGreaterEqual(
            len(call_times), 3,
            f"Hunt mode trop lent : {len(call_times)} probes en 3.5s (attendu ≥3). "
            f"Bug probable : wait timeout non adapté au mode hunt.",
        )

    def test_hunt_mode_intervals_under_2s(self):
        """Intervalles entre probes en hunt mode doivent être < 2s."""
        call_times = []

        def fake_find(*args):
            call_times.append(time.time())
            return []

        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = threading.Lock()
        stop = threading.Event()
        hunt = threading.Event()
        hunt.set()

        with patch("swigi.daemon.find_all_devices", side_effect=fake_find), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(4.0)
            stop.set()
            t.join(timeout=2)

        if len(call_times) >= 2:
            intervals = [call_times[i + 1] - call_times[i] for i in range(len(call_times) - 1)]
            max_interval = max(intervals)
            self.assertLess(
                max_interval, 2.0,
                f"Intervalle max = {max_interval:.2f}s (attendu < 2s en hunt mode)",
            )

    def test_normal_mode_probes_at_5s(self):
        """Hors hunt mode, probe toutes les ~5s (pas 1s — éviter CPU busy-loop)."""
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

        with patch("swigi.daemon.find_all_devices", side_effect=fake_find), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(3.0)  # 3s → moins d'un intervalle normal (5s)
            stop.set()
            t.join(timeout=2)

        # En mode normal, 3s < 5s : au plus 1 probe (le premier immédiat ne se produit pas
        # car wait(5s) bloque directement). 0 probe attendus en 3s.
        self.assertLessEqual(
            len(call_times), 1,
            f"Mode normal : {len(call_times)} probes en 3s (devrait être ≤1)",
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
        mouse.pid = 0xB042

        call_count = [0]

        def fake_find(*args):
            call_count[0] += 1
            return [mouse] if call_count[0] >= 3 else []  # souris arrive au 3e probe

        with patch("swigi.daemon.find_all_devices", side_effect=fake_find), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=None):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()  # hunt mode : 1s entre probes

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(4.0)  # 4s → ≥3 probes à 1s d'intervalle
            stop.set()
            t.join(timeout=2)

        self.assertIn(mouse, mice_list, "Souris jamais trouvée après arrivée tardive")
        self.assertEqual(state["mouse"], "MX Master 4")

    def test_mouse_absent_then_arrives_pending_host_applied(self):
        """Souris absente au démarrage, pending_host actif → correction appliquée à l'arrivée."""
        mice_list = []
        # pending_host = 0 (Mac A) — souris doit revenir ici
        state = {"pending_host": (0, time.time() + 60), "mouse": None, "mice": []}
        lock = threading.Lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042

        call_count = [0]
        sent_hosts = []

        def fake_find(*args):
            call_count[0] += 1
            return [mouse] if call_count[0] >= 2 else []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent_hosts.append(target_host)

        # Simuler : souris sur hôte 1 (Mac B), doit aller vers hôte 0 (Mac A)
        with patch("swigi.daemon.find_all_devices", side_effect=fake_find), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=1), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()

            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(3.0)
            stop.set()
            t.join(timeout=2)

        self.assertIn(0, sent_hosts, "Correction pending_host non envoyée à l'arrivée de la souris")


# ── Scénario 3 Macs complet ───────────────────────────────────────────────────

class TestThreeMacFullScenario(unittest.TestCase):
    """Tests d'intégration : chaîne de switch A(0)→B(1)→C(2)→A(0) avec 3 SwiGi."""

    def _make_lock(self):
        return threading.Lock()

    def test_switch_A_to_B_send_mouse_host1(self):
        """Mac A switch vers B : souris reçoit CHANGE_HOST vers hôte 1."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042
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
        mouse.pid = 0xB042
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
        state = {"pending_host": (0, time.time() + 60), "mouse": None, "mice": []}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        with patch("swigi.daemon.find_all_devices", return_value=[mouse]), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=1), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(1.0)
            stop.set()
            t.join(timeout=2)

        self.assertIn(0, sent, "Correction vers hôte 0 (Mac A) non envoyée")

    def test_3mac_round_trip_pending_host_chain(self):
        """A→B→C→A : vérifier la chaîne complète de pending_host sur Mac A."""
        # Étape 1 : Mac A envoie souris vers B lors du switch A→B
        mouse_AB = _make_mouse(name="MX Master 4")
        mouse_AB.pid = 0xB042
        mice_A = [mouse_AB]
        state_A = {"pending_host": None, "mouse": "MX Master 4", "mice": ["MX Master 4"]}
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
        kb = _make_kb(name="MX Keys S")
        with patch("swigi.daemon.get_current_host", return_value=0):
            from swigi.daemon import _resync_pending_host_from_keyboard
            _resync_pending_host_from_keyboard(kb, state_A)

        self.assertEqual(state_A["pending_host"][0], 0)

        # Étape 3 : souris reconnectée sur Mac A, pas de désync (déjà sur hôte 0)
        mouse_return = _make_mouse(name="MX Master 4")
        mouse_return.pid = 0xB042
        sent_correction = []

        def fake_send_correction(transport, devnum, feat_idx, target_host):
            sent_correction.append(target_host)

        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send_correction):
            result = _check_and_apply_pending_host(mouse_return, state_A)

        self.assertFalse(result, "Aucune correction attendue — souris déjà sur Mac A")
        self.assertEqual(sent_correction, [], "Correction envoyée à tort")
        self.assertIsNone(state_A["pending_host"])

    def test_3mac_desync_corrected_on_return(self):
        """Retour sur Mac A : souris bloquée sur hôte 2 (Mac C) → correction vers 0."""
        kb = _make_kb()
        state = {"pending_host": _pending(2), "mouse": None}

        # Clavier revenu sur hôte 0
        with patch("swigi.daemon.get_current_host", return_value=0):
            from swigi.daemon import _resync_pending_host_from_keyboard
            _resync_pending_host_from_keyboard(kb, state)

        self.assertEqual(state["pending_host"][0], 0)

        # Souris sur hôte 2 → correction vers 0
        mouse = _make_mouse()
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        with patch("swigi.daemon.get_current_host", return_value=2), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send):
            result = _check_and_apply_pending_host(mouse, state)

        self.assertTrue(result)
        self.assertEqual(sent, [0])

    def test_two_mice_both_sent_on_switch(self):
        """2 souris présentes (2 périphériques Logitech) → les deux reçoivent CHANGE_HOST."""
        m1 = _make_mouse(change_host_idx=9, name="MX Master 4")
        m1.pid = 0xB042
        m2 = _make_mouse(change_host_idx=11, name="MX Anywhere 3")
        m2.pid = 0xB028
        mice_list = [m1, m2]
        state = {"pending_host": None, "mouse": "MX Master 4", "mice": ["MX Master 4", "MX Anywhere 3"]}
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
        state = {"pending_host": (1, time.time() + 60), "mouse": None, "mice": []}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042
        sent = []

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)

        # Souris sur hôte 0 (Mac A), pending dit hôte 1 (Mac B)
        with patch("swigi.daemon.find_all_devices", return_value=[mouse]), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(1.0)
            stop.set()
            t.join(timeout=2)

        self.assertIn(1, sent, "Correction vers hôte 1 non envoyée malgré pending_host")

    def test_rapid_switch_second_supersedes_first(self):
        """Switch rapide A→B puis B→C : la 2e cible (C) doit être préservée dans pending."""
        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042
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

        self.assertEqual(state["pending_host"][0], 2, "pending_host doit refléter la 2e cible (C)")

    def test_hunt_probe_after_switch_finds_mouse(self):
        """Après switch, hunt_trigger set → probe loop détecte la souris rapidement."""
        mice_list = []
        state = {"pending_host": (1, time.time() + 60), "mouse": None, "mice": []}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042

        arrival = [None]
        call_count = [0]

        def fake_find(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return []  # 1er probe : pas encore là
            arrival[0] = time.time()
            return [mouse]

        start = time.time()

        with patch("swigi.daemon.find_all_devices", side_effect=fake_find), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=1):  # sync OK
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(3.0)
            stop.set()
            t.join(timeout=2)

        self.assertIsNotNone(arrival[0], "Souris jamais trouvée")
        elapsed = arrival[0] - start
        self.assertLess(elapsed, 3.0, f"Souris trouvée trop tard ({elapsed:.1f}s, attendu < 3s)")
        self.assertIsNone(state["pending_host"], "pending_host non effacé après sync OK")

    def test_state_mouse_updated_after_probe(self):
        """state['mouse'] mis à jour après détection souris par probe loop."""
        mice_list = []
        state = {"pending_host": None, "mouse": None, "mice": []}
        lock = self._make_lock()

        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042

        with patch("swigi.daemon.find_all_devices", return_value=[mouse]), \
             patch("swigi.daemon.notify"), \
             patch("swigi.daemon.get_current_host", return_value=None):
            stop = threading.Event()
            hunt = threading.Event()
            hunt.set()
            t = threading.Thread(
                target=_mice_probe_loop,
                args=(mice_list, state, stop, hunt, lock),
                daemon=True,
            )
            t.start()
            time.sleep(0.5)
            stop.set()
            t.join(timeout=2)

        self.assertEqual(state["mouse"], "MX Master 4")
        self.assertIn("MX Master 4", state["mice"])


# ── _update_kb_state ──────────────────────────────────────────────────────────

class TestUpdateKbState(unittest.TestCase):

    def test_first_active_kb_name_set(self):
        state = {"kbs": {0xB35B: {"name": "MX Keys S", "ok": True}}}
        from swigi.daemon import _update_kb_state
        _update_kb_state(state)
        self.assertEqual(state["kb"], "MX Keys S")

    def test_skips_down_kb_returns_active(self):
        state = {"kbs": {
            0xB35B: {"name": "MX Keys S", "ok": False},
            0xB361: {"name": "MX Keys Mini", "ok": True},
        }}
        from swigi.daemon import _update_kb_state
        _update_kb_state(state)
        self.assertEqual(state["kb"], "MX Keys Mini")

    def test_all_down_sets_none(self):
        state = {"kbs": {0xB35B: {"name": "MX Keys S", "ok": False}}}
        from swigi.daemon import _update_kb_state
        _update_kb_state(state)
        self.assertIsNone(state["kb"])

    def test_empty_kbs_sets_none(self):
        state = {"kbs": {}}
        from swigi.daemon import _update_kb_state
        _update_kb_state(state)
        self.assertIsNone(state["kb"])

    def test_with_state_lock(self):
        state = {
            "_state_lock": threading.Lock(),
            "kbs": {0xB35B: {"name": "MX Keys S", "ok": True}},
        }
        from swigi.daemon import _update_kb_state
        _update_kb_state(state)
        self.assertEqual(state["kb"], "MX Keys S")

    def test_no_kbs_key_sets_none(self):
        state = {}
        from swigi.daemon import _update_kb_state
        _update_kb_state(state)
        self.assertIsNone(state["kb"])


# ── _watch_keyboard : ping fail + reconnect ───────────────────────────────────

class TestWatchKeyboardPingFailReconnect(unittest.TestCase):
    """Teste le chemin ping-fail → reconnect → KbReconnected (critique pour le switch)."""

    def test_ping_fail_emits_kb_reconnected_and_sets_hunt(self):
        """Ping fail → clavier marqué down → reconnect → KbReconnected dans queue."""
        import queue as q_module
        from swigi.daemon import _KbReconnected

        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb_old = _make_kb(name="MX Keys S")
        kb_old.pid = 0xB35B
        # Premier write OK, second lève TransportError (ping fail)
        write_calls = [0]
        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("ping dead")
        kb_old.transport.write.side_effect = write_side
        kb_old.transport.read.side_effect = lambda *a, **kw: None
        kb_old.transport.is_open = True

        kb_new = _make_kb(name="MX Keys S")
        kb_new.pid = 0xB35B
        kb_new.transport.write.return_value = None
        kb_new.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "kbs": {kb_old.pid: {"name": kb_old.name, "ok": True}},
            "pending_host": None,
        }

        with patch("swigi.daemon._find_kb_by_pid", return_value=kb_new), \
             patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(kb_old, event_q, state, stop, hunt),
                daemon=True,
            )
            t.start()
            events = []
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    events.append(event_q.get(timeout=0.2))
                    break
                except q_module.Empty:
                    pass
            stop.set()
            t.join(timeout=3)

        reconnected = [e for e in events if isinstance(e, _KbReconnected)]
        self.assertGreater(len(reconnected), 0, "_KbReconnected non émis après ping fail")
        self.assertTrue(state["kbs"][kb_new.pid]["ok"])

    def test_ping_fail_post_switch_no_disconnect_notify(self):
        """Ping fail juste après un switch → notification 'déconnecté' supprimée (normal)."""
        import queue as q_module

        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B

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
        kb.transport.write.side_effect = write_side
        kb.transport.read.side_effect = lambda *a, **kw: next(_reads, None)
        kb.transport.is_open = True

        kb_new = _make_kb(name="MX Keys S")
        kb_new.pid = 0xB35B
        kb_new.transport.write.return_value = None
        kb_new.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "kbs": {kb.pid: {"name": kb.name, "ok": True}},
            "pending_host": None,
        }

        notifications = []
        with patch("swigi.daemon._find_kb_by_pid", return_value=kb_new), \
             patch("swigi.daemon.get_current_host", return_value=1), \
             patch("swigi.daemon.notify", side_effect=lambda msg, *a: notifications.append(msg)):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(kb, event_q, state, stop, hunt),
                daemon=True,
            )
            t.start()
            time.sleep(1.5)
            stop.set()
            t.join(timeout=3)

        disconnect_notifs = [n for n in notifications if "déconnecté" in n.lower()]
        self.assertEqual(disconnect_notifs, [], f"Notification 'déconnecté' inattendue post-switch : {disconnect_notifs}")

    def test_ping_fail_reconnect_triggers_hunt(self):
        """Reconnect clavier → hunt_trigger.set() pour probe rapide souris."""
        import queue as q_module

        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb_old = _make_kb(name="MX Keys S")
        kb_old.pid = 0xB35B
        write_calls = [0]
        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("dead")
        kb_old.transport.write.side_effect = write_side
        kb_old.transport.read.side_effect = lambda *a, **kw: None

        kb_new = _make_kb(name="MX Keys S")
        kb_new.pid = 0xB35B
        kb_new.transport.write.return_value = None
        kb_new.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "kbs": {kb_old.pid: {"name": kb_old.name, "ok": True}},
            "pending_host": None,
        }

        with patch("swigi.daemon._find_kb_by_pid", return_value=kb_new), \
             patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(kb_old, event_q, state, stop, hunt),
                daemon=True,
            )
            t.start()
            hunt.wait(timeout=3)
            stop.set()
            t.join(timeout=3)

        self.assertTrue(hunt.is_set(), "hunt_trigger non levé après reconnect clavier")

    def test_read_transport_error_in_window_breaks_gracefully(self):
        """TransportError pendant read dans fenêtre 80ms → break, pas de crash."""
        import queue as q_module
        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B
        read_calls = [0]
        def read_side(*a, **kw):
            read_calls[0] += 1
            if read_calls[0] == 1:
                raise TransportError("read dead")
            return None
        kb.transport.write.return_value = None
        kb.transport.read.side_effect = read_side

        state = {
            "kbs": {kb.pid: {"name": kb.name, "ok": True}},
            "pending_host": None,
        }

        t = threading.Thread(
            target=_watch_keyboard,
            args=(kb, event_q, state, stop, hunt),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        stop.set()
        t.join(timeout=2)
        # Pas d'exception propagée — test passe si le thread s'est terminé proprement

    def test_bad_rid_message_skipped(self):
        """Message avec rid inconnu (0xFF) → ignoré, pas de crash."""
        import queue as q_module
        from swigi.constants import MSG_LONG_LEN
        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B

        bad_msg = bytearray(MSG_LONG_LEN)
        bad_msg[0] = 0xFF  # rid inconnu
        _reads = iter([bytes(bad_msg)])
        kb.transport.write.return_value = None
        kb.transport.read.side_effect = lambda *a, **kw: next(_reads, None)

        state = {
            "kbs": {kb.pid: {"name": kb.name, "ok": True}},
            "pending_host": None,
        }

        t = threading.Thread(
            target=_watch_keyboard,
            args=(kb, event_q, state, stop, hunt),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        stop.set()
        t.join(timeout=2)
        self.assertTrue(event_q.empty(), "Aucun event attendu pour message rid=0xFF")

    def test_ping_fail_reconnect_backoff_multiple_attempts(self):
        """Reconnect après 2 échecs (backoff) → finalement reconnecté, hunt levé."""
        import queue as q_module
        from swigi.daemon import _KbReconnected

        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb_old = _make_kb(name="MX Keys S")
        kb_old.pid = 0xB35B
        write_calls = [0]
        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("dead")
        kb_old.transport.write.side_effect = write_side
        kb_old.transport.read.side_effect = lambda *a, **kw: None

        kb_new = _make_kb(name="MX Keys S")
        kb_new.pid = 0xB35B
        kb_new.transport.write.return_value = None
        kb_new.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "kbs": {kb_old.pid: {"name": kb_old.name, "ok": True}},
            "pending_host": None,
        }

        # 2 tentatives None → backoff lines 272-273, puis succès
        attempts = [0]
        def find_side(pid):
            attempts[0] += 1
            return None if attempts[0] < 3 else kb_new

        with patch("swigi.daemon._find_kb_by_pid", side_effect=find_side), \
             patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(kb_old, event_q, state, stop, hunt),
                daemon=True,
            )
            t.start()
            hunt.wait(timeout=6)
            stop.set()
            t.join(timeout=3)

        self.assertTrue(hunt.is_set(), "hunt_trigger non levé après reconnect avec backoff")
        self.assertGreaterEqual(attempts[0], 3)

    def test_ping_fail_stop_during_reconnect_exits_cleanly(self):
        """Stop pendant reconnect (find toujours None) → thread se termine proprement."""
        import queue as q_module

        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb_old = _make_kb(name="MX Keys S")
        kb_old.pid = 0xB35B
        write_calls = [0]
        def write_side(msg):
            write_calls[0] += 1
            if write_calls[0] >= 2:
                raise TransportError("dead")
        kb_old.transport.write.side_effect = write_side
        kb_old.transport.read.side_effect = lambda *a, **kw: None

        state = {
            "kbs": {kb_old.pid: {"name": kb_old.name, "ok": True}},
            "pending_host": None,
        }

        def stop_after():
            time.sleep(0.8)
            stop.set()

        stopper = threading.Thread(target=stop_after, daemon=True)

        with patch("swigi.daemon._find_kb_by_pid", return_value=None), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(
                target=_watch_keyboard,
                args=(kb_old, event_q, state, stop, hunt),
                daemon=True,
            )
            t.start()
            stopper.start()
            t.join(timeout=4)
            stopper.join(timeout=2)

        self.assertFalse(t.is_alive(), "Thread _watch_keyboard bloqué (stop ignoré)")
        self.assertFalse(state["kbs"][kb_old.pid]["ok"])

    def test_non_switch_notification_logged_not_dispatched(self):
        """Notification feature inconnue, sw_id=0 → loggée, aucun SwitchEvent."""
        import queue as q_module
        from swigi.constants import REPORT_LONG, MSG_LONG_LEN
        from swigi.daemon import _SwitchEvent
        event_q = q_module.Queue()
        stop = threading.Event()
        hunt = threading.Event()

        kb = _make_kb(change_host_idx=5, name="MX Keys S")
        kb.pid = 0xB35B

        # Feature 0x20 (pas CHANGE_HOST), sw_id=0
        notif = bytearray(MSG_LONG_LEN)
        notif[0] = REPORT_LONG
        notif[2] = 0x20  # feature différente de change_host_idx=5
        notif[3] = 0x00  # sw_id = 0
        _reads = iter([bytes(notif)])
        kb.transport.write.return_value = None
        kb.transport.read.side_effect = lambda *a, **kw: next(_reads, None)

        state = {
            "kbs": {kb.pid: {"name": kb.name, "ok": True}},
            "pending_host": None,
        }

        t = threading.Thread(
            target=_watch_keyboard,
            args=(kb, event_q, state, stop, hunt),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        stop.set()
        t.join(timeout=2)

        switch_events = []
        while not event_q.empty():
            e = event_q.get_nowait()
            if isinstance(e, _SwitchEvent):
                switch_events.append(e)
        self.assertEqual(switch_events, [], "SwitchEvent inattendu pour notification non-CHANGE_HOST")


# ── run_daemon : intégration dispatcher ──────────────────────────────────────

class TestRunDaemon(unittest.TestCase):
    """Tests de run_daemon : initialisation état, dispatch SwitchEvent, KbReconnected."""

    def test_switch_event_dispatches_change_host_to_mouse(self):
        """SwitchEvent dans queue → _send_to_all_mice appelé, switches comptabilisé."""
        from swigi.daemon import run_daemon, _SwitchEvent

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B
        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042

        stop = threading.Event()
        state = {"pending_host": None}
        sent = []

        def fake_watch_kb(kb, event_q, state, stop_event, hunt_trigger):
            time.sleep(0.05)
            event_q.put(_SwitchEvent(1, kb.name))
            stop_event.wait()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop_event.wait()

        def fake_send(transport, devnum, feat_idx, target_host):
            sent.append(target_host)
            stop.set()

        with patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb), \
             patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(target=run_daemon, args=([kb], [mouse], state, stop), daemon=True)
            t.start()
            t.join(timeout=3)

        self.assertIn(1, sent, "CHANGE_HOST vers hôte 1 non envoyé")
        self.assertGreater(state.get("switches", 0), 0)

    def test_kb_reconnected_event_updates_kb_state(self):
        """KbReconnected dans queue → state['kb'] mis à jour."""
        from swigi.daemon import run_daemon, _KbReconnected

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B
        mouse = _make_mouse(name="MX Master 4")

        stop = threading.Event()
        state = {"pending_host": None}

        def fake_watch_kb(kb_obj, event_q, state, stop_event, hunt_trigger):
            time.sleep(0.05)
            event_q.put(_KbReconnected(kb_obj.name))
            time.sleep(0.2)
            stop_event.set()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop_event.wait()

        with patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb), \
             patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(target=run_daemon, args=([kb], [mouse], state, stop), daemon=True)
            t.start()
            t.join(timeout=3)

        self.assertIsNotNone(state.get("kb"))

    def test_state_initialised_with_keyboards_and_mice(self):
        """run_daemon initialise state['kb'], state['mouse'], state['mice']."""
        from swigi.daemon import run_daemon

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B
        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042

        stop = threading.Event()
        state = {"pending_host": None}

        def fake_watch_kb(kb_obj, event_q, state, stop_event, hunt_trigger):
            stop_event.wait()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop.set()

        with patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb), \
             patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(target=run_daemon, args=([kb], [mouse], state, stop), daemon=True)
            t.start()
            t.join(timeout=3)

        self.assertEqual(state["kb"], "MX Keys S")
        self.assertEqual(state["mouse"], "MX Master 4")
        self.assertIn("MX Master 4", state["mice"])
        self.assertIn("_state_lock", state)

    def test_run_daemon_no_mice_state_mouse_is_none(self):
        """run_daemon avec mice=[] → state['mouse'] = None (démarrage sans souris)."""
        from swigi.daemon import run_daemon

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B

        stop = threading.Event()
        state = {"pending_host": None}

        def fake_watch_kb(kb_obj, event_q, state, stop_event, hunt_trigger):
            stop_event.wait()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop.set()

        with patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb), \
             patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(target=run_daemon, args=([kb], [], state, stop), daemon=True)
            t.start()
            t.join(timeout=3)

        self.assertIsNone(state["mouse"])
        self.assertEqual(state["mice"], [])

    def test_two_switches_increments_counter(self):
        """Deux SwitchEvent → state['switches'] = 2."""
        from swigi.daemon import run_daemon, _SwitchEvent

        kb = _make_kb(name="MX Keys S")
        kb.pid = 0xB35B
        mouse = _make_mouse(name="MX Master 4")
        mouse.pid = 0xB042

        stop = threading.Event()
        state = {"pending_host": None}
        switch_count = [0]

        def fake_watch_kb(kb_obj, event_q, state, stop_event, hunt_trigger):
            time.sleep(0.05)
            event_q.put(_SwitchEvent(1, kb_obj.name))
            time.sleep(0.1)
            event_q.put(_SwitchEvent(0, kb_obj.name))
            time.sleep(0.2)
            stop_event.set()

        def fake_probe(mice, state, stop_event, hunt_trigger, mouse_lock):
            stop_event.wait()

        def fake_send(transport, devnum, feat_idx, target_host):
            switch_count[0] += 1

        with patch("swigi.daemon._watch_keyboard", side_effect=fake_watch_kb), \
             patch("swigi.daemon._mice_probe_loop", side_effect=fake_probe), \
             patch("swigi.daemon.send_change_host", side_effect=fake_send), \
             patch("swigi.daemon.notify"):
            t = threading.Thread(target=run_daemon, args=([kb], [mouse], state, stop), daemon=True)
            t.start()
            t.join(timeout=4)

        self.assertEqual(state.get("switches", 0), 2)


if __name__ == "__main__":
    unittest.main()
