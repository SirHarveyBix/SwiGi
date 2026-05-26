import sys
import unittest
from unittest.mock import MagicMock, patch

_mock_loader = MagicMock()
_mock_loader.lib = MagicMock()
_mock_loader.hid_err = MagicMock(return_value="mock error")
_mock_loader.DeviceInfoStruct = MagicMock()
sys.modules.setdefault("swigi.hidapi_loader", _mock_loader)

_mock_gui = MagicMock()
_mock_gui.notify = MagicMock()
_mock_gui.prefs = {}
sys.modules.setdefault("swigi.gui", _mock_gui)

from swigi.discovery import _clean_name, DeviceInfo, find_all_devices, find_device  # noqa: E402
from swigi.constants import DEVICE_TYPE_KEYBOARD, DEVICE_TYPE_MOUSE  # noqa: E402
from swigi.transport import TransportError  # noqa: E402


# ── _clean_name ────────────────────────────────────────────────────────────────

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


# ── DeviceInfo.close ───────────────────────────────────────────────────────────

class TestDeviceInfoClose(unittest.TestCase):

    def _make_di(self):
        t = MagicMock()
        t.is_open = True
        return DeviceInfo(transport=t, name="MX Keys S", pid=0xB35B, change_host_idx=5), t

    def test_close_calls_transport_close(self):
        di, t = self._make_di()
        di.close()
        t.close.assert_called_once()

    def test_close_swallows_os_error(self):
        di, t = self._make_di()
        t.close.side_effect = OSError("device gone")
        di.close()  # ne doit pas lever

    def test_close_swallows_transport_error(self):
        di, t = self._make_di()
        t.close.side_effect = TransportError("dead")
        di.close()  # ne doit pas lever


# ── find_all_devices ───────────────────────────────────────────────────────────

def _make_hid_node(pid, usage_page=0xFF00, usage=0x0002, path=b"/dev/hid0"):
    """Construit un faux nœud de liste chaînée HID."""
    info = MagicMock()
    info.product_id = pid
    info.usage_page = usage_page
    info.usage = usage
    info.path = path
    info.next = None  # termine la liste
    node = MagicMock()
    node.contents = info
    return node


class TestFindAllDevices(unittest.TestCase):

    def setUp(self):
        _mock_loader.lib.reset_mock()

    def test_empty_enumeration_returns_empty_list(self):
        _mock_loader.lib.hid_enumerate.return_value = None
        result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_receiver_pid_skipped(self):
        node = _make_hid_node(pid=0xC548)  # BOLT_PID → receveur
        _mock_loader.lib.hid_enumerate.return_value = node
        result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_wrong_usage_pair_skipped(self):
        node = _make_hid_node(pid=0xB35B, usage_page=0x0099, usage=0x9999)
        _mock_loader.lib.hid_enumerate.return_value = node
        result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_transport_open_fails_skipped(self):
        node = _make_hid_node(pid=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = node
        with patch("swigi.discovery.HIDTransport", side_effect=OSError("no")):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_no_device_type_feature_skipped(self):
        node = _make_hid_node(pid=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = node
        with patch("swigi.discovery.HIDTransport"), \
             patch("swigi.discovery.resolve_feature", return_value=None):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_no_change_host_feature_skipped(self):
        node = _make_hid_node(pid=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = node
        with patch("swigi.discovery.HIDTransport"), \
             patch("swigi.discovery.resolve_feature", side_effect=[5, None]), \
             patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD), \
             patch("swigi.discovery.get_device_name", return_value="MX Keys S"):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_wrong_device_type_skipped(self):
        node = _make_hid_node(pid=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = node
        with patch("swigi.discovery.HIDTransport"), \
             patch("swigi.discovery.resolve_feature", side_effect=[5, 9]), \
             patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_MOUSE), \
             patch("swigi.discovery.get_device_name", return_value="MX Master 4"):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_found_keyboard(self):
        node = _make_hid_node(pid=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = node
        mock_t = MagicMock()
        with patch("swigi.discovery.HIDTransport", return_value=mock_t), \
             patch("swigi.discovery.resolve_feature", side_effect=[5, 9]), \
             patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD), \
             patch("swigi.discovery.get_device_name", return_value="MX Keys S"):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "MX Keys S")
        self.assertEqual(result[0].pid, 0xB35B)
        self.assertEqual(result[0].change_host_idx, 9)

    def test_found_mouse(self):
        node = _make_hid_node(pid=0xB042, usage_page=0x0001, usage=0x0002)
        _mock_loader.lib.hid_enumerate.return_value = node
        mock_t = MagicMock()
        with patch("swigi.discovery.HIDTransport", return_value=mock_t), \
             patch("swigi.discovery.resolve_feature", side_effect=[3, 11]), \
             patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_MOUSE), \
             patch("swigi.discovery.get_device_name", return_value="MX Master 4"):
            result = find_all_devices(DEVICE_TYPE_MOUSE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "MX Master 4")

    def test_duplicate_pid_deduplication(self):
        """2 nœuds avec même PID → 1 seul DeviceInfo, doublon fermé."""
        info1 = MagicMock()
        info1.product_id = 0xB35B
        info1.usage_page = 0xFF00
        info1.usage = 0x0002
        info1.path = b"/dev/hid0"

        info2 = MagicMock()
        info2.product_id = 0xB35B
        info2.usage_page = 0xFF43
        info2.usage = 0x0202
        info2.path = b"/dev/hid1"
        info2.next = None

        # node1 → node2 → None
        node2 = MagicMock()
        node2.contents = info2
        info1.next = node2

        node1 = MagicMock()
        node1.contents = info1

        _mock_loader.lib.hid_enumerate.return_value = node1

        mock_t = MagicMock()
        with patch("swigi.discovery.HIDTransport", return_value=mock_t), \
             patch("swigi.discovery.resolve_feature", side_effect=[5, 9, 5, 9]), \
             patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD), \
             patch("swigi.discovery.get_device_name", return_value="MX Keys S"):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)

        self.assertEqual(len(result), 1)

    def test_transport_error_during_resolve_skipped(self):
        node = _make_hid_node(pid=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = node
        with patch("swigi.discovery.HIDTransport"), \
             patch("swigi.discovery.resolve_feature", side_effect=TransportError("dead")):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])


# ── find_device ────────────────────────────────────────────────────────────────

class TestFindDevice(unittest.TestCase):

    def test_returns_first_result(self):
        mock_di = MagicMock()
        with patch("swigi.discovery.find_all_devices", return_value=[mock_di]):
            result = find_device(DEVICE_TYPE_KEYBOARD)
        self.assertIs(result, mock_di)

    def test_returns_none_when_empty(self):
        with patch("swigi.discovery.find_all_devices", return_value=[]):
            result = find_device(DEVICE_TYPE_KEYBOARD)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
