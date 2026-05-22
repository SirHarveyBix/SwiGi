import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Mock hidapi_loader + gui AVANT tout import swigi (effets de bord)
_mock_loader = MagicMock()
_mock_loader.lib = MagicMock()
_mock_loader.hid_err = MagicMock(return_value="mock error")
_mock_loader.DeviceInfoStruct = MagicMock()
sys.modules.setdefault("swigi.hidapi_loader", _mock_loader)

_mock_gui = MagicMock()
_mock_gui.notify = MagicMock()
_mock_gui.HAS_RUMPS = False
_mock_gui.SwiGiMenuBar = None
sys.modules.setdefault("swigi.gui", _mock_gui)

from swigi.daemon import _check_and_apply_pending_host  # noqa: E402
from swigi.transport import TransportError              # noqa: E402


def _make_mouse(change_host_idx=9):
    """Construit un DeviceInfo mock avec transport ouvert."""
    mouse = MagicMock()
    mouse.change_host_idx = change_host_idx
    mouse.transport.is_open = True
    return mouse


class TestCheckAndApplyPendingHost(unittest.TestCase):
    """Teste _check_and_apply_pending_host — 6 cas."""

    def test_no_pending_returns_false(self):
        mouse = _make_mouse()
        state = {"pending_host": None, "mouse": "MX Master 4"}
        result = _check_and_apply_pending_host(mouse, state)
        self.assertFalse(result)
        mouse.close.assert_not_called()

    def test_expired_ttl_clears_and_returns_false(self):
        mouse = _make_mouse()
        state = {"pending_host": (1, time.time() - 1.0), "mouse": "MX Master 4"}
        result = _check_and_apply_pending_host(mouse, state)
        self.assertFalse(result)
        self.assertIsNone(state["pending_host"])
        mouse.close.assert_not_called()

    def test_sync_ok_clears_pending_returns_false(self):
        mouse = _make_mouse()
        state = {"pending_host": (1, time.time() + 60), "mouse": "MX Master 4"}
        with patch("swigi.daemon.get_current_host", return_value=1):
            result = _check_and_apply_pending_host(mouse, state)
        self.assertFalse(result)
        self.assertIsNone(state["pending_host"])
        mouse.close.assert_not_called()

    def test_get_current_host_none_keeps_pending(self):
        mouse = _make_mouse()
        state = {"pending_host": (1, time.time() + 60), "mouse": "MX Master 4"}
        with patch("swigi.daemon.get_current_host", return_value=None):
            result = _check_and_apply_pending_host(mouse, state)
        self.assertFalse(result)
        self.assertIsNotNone(state["pending_host"])  # gardé pour prochain reconnect
        mouse.close.assert_not_called()

    def test_desync_sends_correction_closes_mouse(self):
        mouse = _make_mouse()
        state = {"pending_host": (1, time.time() + 60), "mouse": "MX Master 4"}
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host") as mock_send:
            result = _check_and_apply_pending_host(mouse, state)
        self.assertTrue(result)
        mock_send.assert_called_once()
        mouse.close.assert_called_once()
        self.assertIsNone(state["mouse"])
        self.assertIsNone(state["pending_host"])

    def test_desync_correction_fails_keeps_pending_closes_mouse(self):
        """Correction échouée → mouse fermée, pending_host conservé pour retry."""
        mouse = _make_mouse()
        state = {"pending_host": (1, time.time() + 60), "mouse": "MX Master 4"}
        with patch("swigi.daemon.get_current_host", return_value=0), \
             patch("swigi.daemon.send_change_host", side_effect=TransportError("dead")):
            result = _check_and_apply_pending_host(mouse, state)
        self.assertTrue(result)
        mouse.close.assert_called_once()
        self.assertIsNone(state["mouse"])
        self.assertIsNotNone(state["pending_host"])  # conservé pour prochain reconnect


if __name__ == "__main__":
    unittest.main()
