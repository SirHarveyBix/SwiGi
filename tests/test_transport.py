import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock hidapi_loader avant tout import swigi
_mock_loader = MagicMock()
_mock_loader.lib = MagicMock()
_mock_loader.hid_err = MagicMock(return_value="")
sys.modules.setdefault("swigi.hidapi_loader", _mock_loader)

from swigi.transport import HIDTransport, TransportError  # noqa: E402


class TestHIDTransport(unittest.TestCase):

    def _make(self, dev=None):
        if dev is None:
            dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t._lib = lib_mock  # garde le mock pour les assertions post-__init__
        return t, lib_mock

    def test_init_success_is_open(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
        self.assertTrue(t.is_open)

    def test_init_fail_raises_oserror(self):
        with patch("swigi.transport.lib") as lib_mock, \
             patch("swigi.transport.hid_err", return_value="fail"):
            lib_mock.hid_open_path.return_value = None
            with self.assertRaises(OSError):
                HIDTransport(b"/dev/hidraw0", 0xB35B)

    def test_is_open_false_after_close(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t.close()
        self.assertFalse(t.is_open)

    def test_read_returns_bytes_on_success(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_read_timeout.return_value = 4
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = t.read(timeout=100)
        self.assertIsInstance(result, bytes)
        self.assertEqual(len(result), 4)

    def test_read_returns_none_on_timeout(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_read_timeout.return_value = 0
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = t.read()
        self.assertIsNone(result)

    def test_read_raises_transport_error_on_negative(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock, \
             patch("swigi.transport.hid_err", return_value="read error"):
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_read_timeout.return_value = -1
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            with self.assertRaises(TransportError):
                t.read()

    def test_read_returns_none_on_bt_success_quirk(self):
        """n<0 mais error='success' → quirk BT macOS → None."""
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock, \
             patch("swigi.transport.hid_err", return_value="success"):
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_read_timeout.return_value = -1
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = t.read()
        self.assertIsNone(result)

    def test_read_returns_none_on_empty_error(self):
        """n<0 et error='' → None (pas d'exception)."""
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock, \
             patch("swigi.transport.hid_err", return_value=""):
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_read_timeout.return_value = -1
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            result = t.read()
        self.assertIsNone(result)

    def test_read_on_closed_raises(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t.close()
            with self.assertRaises(TransportError):
                t.read()

    def test_write_success(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_write.return_value = 7
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t.write(b"\x11\xff\x00" * 2 + b"\x00")  # pas d'exception

    def test_write_raises_on_failure(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock, \
             patch("swigi.transport.hid_err", return_value="write fail"):
            lib_mock.hid_open_path.return_value = dev
            lib_mock.hid_write.return_value = -1
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            with self.assertRaises(TransportError):
                t.write(b"\x11" * 7)

    def test_write_on_closed_raises(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t.close()
            with self.assertRaises(TransportError):
                t.write(b"\x11" * 7)

    def test_close_idempotent(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t.close()
            t.close()  # pas d'exception

    def test_context_manager(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            with HIDTransport(b"/dev/hidraw0", 0xB35B) as t:
                self.assertTrue(t.is_open)
            self.assertFalse(t.is_open)

    def test_del_closes_transport(self):
        dev = MagicMock()
        with patch("swigi.transport.lib") as lib_mock:
            lib_mock.hid_open_path.return_value = dev
            t = HIDTransport(b"/dev/hidraw0", 0xB35B)
            t.__del__()
        self.assertFalse(t.is_open)


if __name__ == "__main__":
    unittest.main()
