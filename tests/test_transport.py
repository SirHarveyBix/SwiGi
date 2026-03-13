import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock hidapi_loader avant tout import swigi
if "swigi.hidapi_loader" in sys.modules:
    _mock_loader = sys.modules["swigi.hidapi_loader"]
else:
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    _mock_loader.hid_error = MagicMock(return_value="")
    sys.modules["swigi.hidapi_loader"] = _mock_loader

from swigi.transport import HIDTransport, TransportError


class TestHIDTransport(unittest.TestCase):
    def _make(self, device=None):
        if device is None:
            device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport._lib = lib_mock  # garde le mock pour les assertions post-__init__
        return transport, lib_mock

    def test_init_success_is_open(self):
        """Transport ouvert avec succès → is_open True."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
        self.assertTrue(transport.is_open)

    def test_init_fail_raises_oserror(self):
        """hid_open_path échoué → OSError."""
        with (
            patch("swigi.transport.lib") as lib_mock,
            patch("swigi.transport.hid_error", return_value="fail"),
        ):
            lib_mock.hid_open_path.return_value = None
            with self.assertRaises(OSError):
                HIDTransport(b"/dev/hidraw0", 0xB35B)

    def test_is_open_false_after_close(self):
        """is_open False après close."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport.close()
        self.assertFalse(transport.is_open)

    def test_read_returns_bytes_on_success(self):
        """Lecture réussie retourne les octets lus."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_read_timeout.return_value = 4
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = transport.read(timeout=100)
        self.assertIsInstance(result, bytes)
        self.assertEqual(len(result), 4)

    def test_read_returns_none_on_timeout(self):
        """Timeout (0 octets lus) retourne None."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_read_timeout.return_value = 0
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = transport.read()
        self.assertIsNone(result)

    def test_read_raises_transport_error_on_negative(self):
        """Erreur de lecture (bytes < 0) lève TransportError."""
        device = MagicMock()
        with (
            patch("swigi.transport.lib") as lib_mock,
            patch("swigi.transport.hid_error", return_value="read error"),
        ):
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_read_timeout.return_value = -1
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            with self.assertRaises(TransportError):
                transport.read()

    def test_read_returns_none_on_bt_success_quirk(self):
        """bytes_read<0 mais error='success' → quirk BT macOS → None."""
        device = MagicMock()
        with (
            patch("swigi.transport.lib") as lib_mock,
            patch("swigi.transport.hid_error", return_value="success"),
        ):
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_read_timeout.return_value = -1
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = transport.read()
        self.assertIsNone(result)

    def test_read_returns_none_on_empty_error(self):
        """bytes_read<0 et error='' → None (pas d'exception)."""
        device = MagicMock()
        with (
            patch("swigi.transport.lib") as lib_mock,
            patch("swigi.transport.hid_error", return_value=""),
        ):
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_read_timeout.return_value = -1
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = transport.read()
        self.assertIsNone(result)

    def test_read_on_closed_raises(self):
        """Lecture sur transport fermé lève TransportError."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport.close()
            with self.assertRaises(TransportError):
                transport.read()

    def test_write_success(self):
        """Écriture réussie ne lève pas d'exception."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_write.return_value = 7
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport.write(b"\x11\xff\x00" * 2 + b"\x00")  # pas d'exception

    def test_write_raises_on_failure(self):
        """Écriture échouée (bytes < 0) lève TransportError."""
        device = MagicMock()
        with (
            patch("swigi.transport.lib") as lib_mock,
            patch("swigi.transport.hid_error", return_value="write fail"),
        ):
            lib_mock.hid_open_path.return_value = device
            lib_mock.hid_write.return_value = -1
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            with self.assertRaises(TransportError):
                transport.write(b"\x11" * 7)

    def test_write_on_closed_raises(self):
        """Écriture sur transport fermé lève TransportError."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport.close()
            with self.assertRaises(TransportError):
                transport.write(b"\x11" * 7)

    def test_close_idempotent(self):
        """Appeler close deux fois ne lève pas d'exception."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport.close()
            transport.close()  # pas d'exception

    def test_context_manager(self):
        """Context manager ferme le transport en sortie de with."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            with HIDTransport(b"/dev/hidraw0", 0xB35B) as transport:
                self.assertTrue(transport.is_open)
            self.assertFalse(transport.is_open)

    def test_del_closes_transport(self):
        """__del__ ferme le transport."""
        device = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = device
            transport = HIDTransport(b"/dev/hidraw0", 0xB35B)
            transport.__del__()
        self.assertFalse(transport.is_open)


if __name__ == "__main__":
    unittest.main()
