import sys
import unittest
from unittest.mock import MagicMock, patch

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

from swigi.constants import DEVICE_TYPE_KEYBOARD, DEVICE_TYPE_MOUSE
from swigi.discovery import DeviceInfo, _clean_name, find_all_devices, find_device
from swigi.transport import TransportError

# ── _clean_name ────────────────────────────────────────────────────────────────


class TestCleanName(unittest.TestCase):
    def test_removes_null_bytes(self):
        """Supprime les octets null du nom."""
        self.assertEqual(_clean_name("MX Keys\x00\x00", 0xB35B), "MX Keys")

    def test_strips_whitespace(self):
        """Supprime les espaces en début et fin."""
        self.assertEqual(_clean_name("  MX Keys  ", 0xB35B), "MX Keys")

    def test_empty_after_clean_returns_fallback(self):
        """Nom vide après nettoyage → fallback avec Product ID."""
        self.assertEqual(_clean_name("\x00\x00", 0xB35B), "Logitech-0xB35B")

    def test_none_returns_fallback(self):
        """None → fallback avec Product ID."""
        self.assertEqual(_clean_name(None, 0xB35B), "Logitech-0xB35B")

    def test_empty_string_returns_fallback(self):
        """Chaîne vide → fallback avec Product ID."""
        self.assertEqual(_clean_name("", 0xB35B), "Logitech-0xB35B")

    def test_normal_name_unchanged(self):
        """Nom normal retourné tel quel."""
        self.assertEqual(_clean_name("MX Master 4", 0xB042), "MX Master 4")


# ── DeviceInfo.close ───────────────────────────────────────────────────────────


class TestDeviceInfoClose(unittest.TestCase):
    def _make_device_info(self):
        transport = MagicMock()
        transport.is_open = True
        return DeviceInfo(
            transport=transport,
            name="MX Keys S",
            product_id=0xB35B,
            change_host_index=5,
        ), transport

    def test_close_calls_transport_close(self):
        """close() appelle transport.close()."""
        device_info, transport = self._make_device_info()
        device_info.close()
        transport.close.assert_called_once()

    def test_close_swallows_os_error(self):
        """close() absorbe OSError sans propager."""
        device_info, transport = self._make_device_info()
        transport.close.side_effect = OSError("device gone")
        device_info.close()  # ne doit pas lever

    def test_close_swallows_transport_error(self):
        """close() absorbe TransportError sans propager."""
        device_info, transport = self._make_device_info()
        transport.close.side_effect = TransportError("dead")
        device_info.close()  # ne doit pas lever


# ── find_all_devices ───────────────────────────────────────────────────────────


def _make_hid_node(product_id, usage_page=0xFF00, usage=0x0002, path=b"/dev/hid0"):
    """Construit un faux nœud de liste chaînée HID."""
    device_info = MagicMock()
    device_info.product_id = product_id
    device_info.usage_page = usage_page
    device_info.usage = usage
    device_info.path = path
    device_info.next = None  # termine la liste
    enumeration_node = MagicMock()
    enumeration_node.contents = device_info
    return enumeration_node


class TestFindAllDevices(unittest.TestCase):
    def setUp(self):
        _mock_loader.lib.reset_mock()

    def test_empty_enumeration_returns_empty_list(self):
        """Énumération vide retourne liste vide."""
        _mock_loader.lib.hid_enumerate.return_value = None
        result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_receiver_pid_skipped(self):
        """Receivers (Bolt/Unifying) ignorés."""
        enumeration_node = _make_hid_node(product_id=0xC548)  # BOLT_PID → receveur
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_wrong_usage_pair_skipped(self):
        """Interfaces HID avec usage_page/usage non reconnus ignorées."""
        enumeration_node = _make_hid_node(
            product_id=0xB35B, usage_page=0x0099, usage=0x9999
        )
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_transport_open_fails_skipped(self):
        """Impossible d'ouvrir le transport → ignoré."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        with patch("swigi.discovery.HIDTransport", side_effect=OSError("no")):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_no_device_type_feature_skipped(self):
        """Pas de feature DEVICE_TYPE_AND_NAME → ignoré."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        with (
            patch("swigi.discovery.HIDTransport"),
            patch("swigi.discovery.resolve_feature", return_value=None),
        ):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_no_change_host_feature_skipped(self):
        """Pas de feature CHANGE_HOST → ignoré."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        with (
            patch("swigi.discovery.HIDTransport"),
            patch("swigi.discovery.resolve_feature", side_effect=[5, None]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD),
            patch("swigi.discovery.get_device_name", return_value="MX Keys S"),
        ):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_no_change_host_logs_warning(self):
        """Ancienne génération sans CHANGE_HOST → warning loggé avec nom et PID."""
        import logging
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        with (
            patch("swigi.discovery.HIDTransport"),
            patch("swigi.discovery.resolve_feature", side_effect=[5, None]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD),
            patch("swigi.discovery.get_device_name", return_value="MX Keys S"),
            self.assertLogs("swigi.discovery", level=logging.WARNING) as log_cm,
        ):
            find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertTrue(
            any("CHANGE_HOST" in line and "0xB35B" in line for line in log_cm.output),
            f"Log attendu avec CHANGE_HOST et PID 0xB35B, reçu : {log_cm.output}",
        )

    def test_wrong_device_type_skipped(self):
        """Device type ne correspond pas au type demandé → ignoré."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        with (
            patch("swigi.discovery.HIDTransport"),
            patch("swigi.discovery.resolve_feature", side_effect=[5, 9]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_MOUSE),
            patch("swigi.discovery.get_device_name", return_value="MX Master 4"),
        ):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])

    def test_found_keyboard(self):
        """Clavier trouvé avec tous les attributs corrects."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        mock_transport = MagicMock()
        with (
            patch("swigi.discovery.HIDTransport", return_value=mock_transport),
            patch("swigi.discovery.resolve_feature", side_effect=[5, 9]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD),
            patch("swigi.discovery.get_device_name", return_value="MX Keys S"),
        ):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "MX Keys S")
        self.assertEqual(result[0].product_id, 0xB35B)
        self.assertEqual(result[0].change_host_index, 9)

    def test_found_mouse(self):
        """Souris trouvée via usage_page Generic Desktop."""
        enumeration_node = _make_hid_node(
            product_id=0xB042, usage_page=0x0001, usage=0x0002
        )
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        mock_transport = MagicMock()
        with (
            patch("swigi.discovery.HIDTransport", return_value=mock_transport),
            patch("swigi.discovery.resolve_feature", side_effect=[3, 11]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_MOUSE),
            patch("swigi.discovery.get_device_name", return_value="MX Master 4"),
        ):
            result = find_all_devices(DEVICE_TYPE_MOUSE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "MX Master 4")

    def test_duplicate_pid_deduplication(self):
        """2 nœuds avec même Product ID → 1 seul DeviceInfo, doublon fermé."""
        device_info1 = MagicMock()
        device_info1.product_id = 0xB35B
        device_info1.usage_page = 0xFF00
        device_info1.usage = 0x0002
        device_info1.path = b"/dev/hid0"

        device_info2 = MagicMock()
        device_info2.product_id = 0xB35B
        device_info2.usage_page = 0xFF43
        device_info2.usage = 0x0202
        device_info2.path = b"/dev/hid1"
        device_info2.next = None

        # node1 → node2 → None
        enumeration_node2 = MagicMock()
        enumeration_node2.contents = device_info2
        device_info1.next = enumeration_node2

        enumeration_node1 = MagicMock()
        enumeration_node1.contents = device_info1

        _mock_loader.lib.hid_enumerate.return_value = enumeration_node1

        mock_transport = MagicMock()
        with (
            patch("swigi.discovery.HIDTransport", return_value=mock_transport),
            patch("swigi.discovery.resolve_feature", side_effect=[5, 9, 5, 9]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD),
            patch("swigi.discovery.get_device_name", return_value="MX Keys S"),
        ):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)

        self.assertEqual(len(result), 1)

    def test_transport_error_during_resolve_skipped(self):
        """TransportError pendant resolve_feature → ignoré."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        with (
            patch("swigi.discovery.HIDTransport"),
            patch(
                "swigi.discovery.resolve_feature", side_effect=TransportError("dead")
            ),
        ):
            result = find_all_devices(DEVICE_TYPE_KEYBOARD)
        self.assertEqual(result, [])


# ── find_device ────────────────────────────────────────────────────────────────


class TestFindDevice(unittest.TestCase):
    def test_returns_first_result(self):
        """Retourne le premier résultat de find_all_devices."""
        mock_device_info = MagicMock()
        with patch("swigi.discovery.find_all_devices", return_value=[mock_device_info]):
            result = find_device(DEVICE_TYPE_KEYBOARD)
        self.assertIs(result, mock_device_info)

    def test_returns_none_when_empty(self):
        """Retourne None si aucun device trouvé."""
        with patch("swigi.discovery.find_all_devices", return_value=[]):
            result = find_device(DEVICE_TYPE_KEYBOARD)
        self.assertIsNone(result)


# ── Drain transport au démarrage ───────────────────────────────────────────────


class TestFindAllDevicesDrainsTransport(unittest.TestCase):
    """Vérifie que _drain_transport est appelé après ouverture du transport HID."""

    def setUp(self):
        _mock_loader.lib.reset_mock()

    def test_drain_called_after_transport_open(self):
        """_drain_transport appelé juste après HIDTransport() pour vider le buffer kernel."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        mock_transport = MagicMock()

        with (
            patch("swigi.discovery.HIDTransport", return_value=mock_transport),
            patch("swigi.discovery.resolve_feature", side_effect=[5, 9]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD),
            patch("swigi.discovery.get_device_name", return_value="MX Keys S"),
            patch("swigi.discovery._drain_transport") as mock_drain,
        ):
            find_all_devices(DEVICE_TYPE_KEYBOARD)

        self.assertGreaterEqual(mock_drain.call_count, 2)
        self.assertEqual(mock_drain.call_args_list[0].args[0], mock_transport)

    def test_drain_called_on_failed_device_too(self):
        """_drain_transport appelé même si le périphérique est du mauvais type (avant fermeture)."""
        enumeration_node = _make_hid_node(product_id=0xB042)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        mock_transport = MagicMock()

        with (
            patch("swigi.discovery.HIDTransport", return_value=mock_transport),
            patch("swigi.discovery.resolve_feature", side_effect=[3, 11]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_MOUSE),
            patch("swigi.discovery.get_device_name", return_value="MX Master 4"),
            patch("swigi.discovery._drain_transport") as mock_drain,
        ):
            result = find_all_devices(
                DEVICE_TYPE_KEYBOARD
            )  # cherche clavier → souris ignorée

        # Drain appelé même si le périphérique est filtré ensuite
        mock_drain.assert_called_once_with(mock_transport)
        self.assertEqual(result, [])

    def test_no_drain_when_transport_open_fails(self):
        """Si HIDTransport() lève OSError, _drain_transport ne doit pas être appelé."""
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node

        with (
            patch("swigi.discovery.HIDTransport", side_effect=OSError("no device")),
            patch("swigi.discovery._drain_transport") as mock_drain,
        ):
            find_all_devices(DEVICE_TYPE_KEYBOARD)

        mock_drain.assert_not_called()

    def test_drain_before_hidpp_requests_prevents_stale_name(self):
        """Sans drain, une réponse stale peut corrompre le nom (MX Keys WirelessMX Keys W).

        Ce test vérifie que get_device_name n'est appelé qu'après _drain_transport.
        """
        call_order = []
        enumeration_node = _make_hid_node(product_id=0xB35B)
        _mock_loader.lib.hid_enumerate.return_value = enumeration_node
        mock_transport = MagicMock()

        def record_drain(transport):
            call_order.append("drain")

        def record_get_name(*args):
            call_order.append("get_device_name")
            return "MX Keys S"

        with (
            patch("swigi.discovery.HIDTransport", return_value=mock_transport),
            patch("swigi.discovery._drain_transport", side_effect=record_drain),
            patch("swigi.discovery.resolve_feature", side_effect=[5, 9]),
            patch("swigi.discovery.get_device_type", return_value=DEVICE_TYPE_KEYBOARD),
            patch("swigi.discovery.get_device_name", side_effect=record_get_name),
        ):
            find_all_devices(DEVICE_TYPE_KEYBOARD)

        self.assertIn("drain", call_order)
        self.assertIn("get_device_name", call_order)
        drain_idx = call_order.index("drain")
        name_idx = call_order.index("get_device_name")
        self.assertLess(
            drain_idx, name_idx, "drain doit être appelé AVANT get_device_name"
        )


if __name__ == "__main__":
    unittest.main()
