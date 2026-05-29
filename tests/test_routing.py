"""Tests pour swigi.discovery — classification Gen S / Legacy."""

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

from swigi.discovery import (
    GENERATION_PULL,
    GENERATION_PUSH,
    classify_generation,
)
from swigi.transport import TransportError


class TestClassifyGeneration(unittest.TestCase):
    @patch("swigi.discovery.get_protocol_version")
    def test_gen_s_detected_version_4_5(self, mock_version):
        """HID++ 4.5 → Gen S (push)."""
        mock_version.return_value = (4, 5)
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PUSH)

    @patch("swigi.discovery.get_protocol_version")
    def test_gen_s_detected_version_5_0(self, mock_version):
        """HID++ 5.0 → Gen S (push)."""
        mock_version.return_value = (5, 0)
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PUSH)

    @patch("swigi.discovery.get_protocol_version")
    def test_legacy_detected_version_2_0(self, mock_version):
        """HID++ 2.0 → Legacy (pull)."""
        mock_version.return_value = (2, 0)
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PULL)

    @patch("swigi.discovery.get_protocol_version")
    def test_legacy_detected_version_4_4(self, mock_version):
        """HID++ 4.4 → Legacy (pull), juste sous le seuil."""
        mock_version.return_value = (4, 4)
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PULL)

    @patch("swigi.discovery.get_protocol_version")
    def test_query_failure_defaults_to_pull(self, mock_version):
        """Si query version retourne None → fallback pull."""
        mock_version.return_value = None
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PULL)

    @patch("swigi.discovery.get_protocol_version")
    def test_transport_error_defaults_to_pull(self, mock_version):
        """TransportError → fallback pull."""
        mock_version.side_effect = TransportError("dead")
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PULL)

    @patch("swigi.discovery.get_protocol_version")
    def test_os_error_defaults_to_pull(self, mock_version):
        """OSError → fallback pull."""
        mock_version.side_effect = OSError("permission denied")
        transport = MagicMock()
        result = classify_generation(transport)
        self.assertEqual(result, GENERATION_PULL)


if __name__ == "__main__":
    unittest.main()
