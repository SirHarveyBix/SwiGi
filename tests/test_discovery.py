import sys
import unittest
from unittest.mock import MagicMock

_mock_loader = MagicMock()
_mock_loader.lib = MagicMock()
_mock_loader.hid_err = MagicMock(return_value="mock error")
_mock_loader.DeviceInfoStruct = MagicMock()
sys.modules.setdefault("swigi.hidapi_loader", _mock_loader)

_mock_gui = MagicMock()
_mock_gui.notify = MagicMock()
_mock_gui.prefs = {}
sys.modules.setdefault("swigi.gui", _mock_gui)

from swigi.discovery import _clean_name  # noqa: E402


class TestCleanName(unittest.TestCase):
    def test_removes_null_bytes(self):
        self.assertEqual(_clean_name("MX Keys\x00\x00", 0xB35B), "MX Keys")

    def test_strips_whitespace(self):
        self.assertEqual(_clean_name("  MX Keys  ", 0xB35B), "MX Keys")

    def test_empty_after_clean_returns_fallback(self):
        self.assertEqual(_clean_name("\x00\x00", 0xB35B), "Logitech-0xB35B")

    def test_none_returns_fallback(self):
        self.assertEqual(_clean_name(None, 0xB35B), "Logitech-0xB35B")

    def test_empty_string_returns_fallback(self):
        self.assertEqual(_clean_name("", 0xB35B), "Logitech-0xB35B")

    def test_normal_name_unchanged(self):
        self.assertEqual(_clean_name("MX Master 4", 0xB042), "MX Master 4")


if __name__ == "__main__":
    unittest.main()
