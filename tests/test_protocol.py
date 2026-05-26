import struct
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock hidapi_loader AVANT d'importer protocol/transport
# Cela permet de lancer les tests sans hidapi installé
if "swigi.hidapi_loader" in sys.modules:
    _mock_loader = sys.modules["swigi.hidapi_loader"]
else:
    _mock_loader = MagicMock()
    _mock_loader.lib = MagicMock()
    _mock_loader.hid_err = MagicMock(return_value="mock error")
    _mock_loader.DeviceInfoStruct = MagicMock()
    sys.modules["swigi.hidapi_loader"] = _mock_loader

from swigi import protocol  # noqa: E402
from swigi.constants import REPORT_LONG, SW_ID  # noqa: E402
from swigi.protocol import get_device_name  # noqa: E402


class MockTransport:
    def __init__(self):
        self.written_messages = []
        self.responses_to_read = []
        self.is_open = True

    def write(self, message: bytes) -> None:
        self.written_messages.append(message)

    def read(self, timeout: int = 500) -> bytes | None:
        if self.responses_to_read:
            return self.responses_to_read.pop(0)
        return None

    def close(self) -> None:
        self.is_open = False


class TestProtocol(unittest.TestCase):
    """Teste la construction et le parsing des messages HID++ 2.0 via MockTransport."""

    def test_build_message(self):
        device_number = 0xFF
        request_id = 0x090A
        parameters = b"\x00\x01\x02"

        message = protocol._build_message(device_number, request_id, parameters)

        self.assertEqual(len(message), 20)
        self.assertEqual(message[0], REPORT_LONG)
        self.assertEqual(message[1], device_number)
        self.assertEqual(message[2:4], struct.pack("!H", request_id))
        self.assertEqual(message[4:7], parameters)
        # padding should be zero
        self.assertEqual(message[7:], b"\x00" * 13)

    def test_pack_parameters(self):
        parameters = [0x01, b"\x02\x03", 0x04]
        packed = protocol._pack_parameters(parameters)
        self.assertEqual(packed, b"\x01\x02\x03\x04")

    def test_hidpp_request_success(self):
        transport = MockTransport()
        device_number = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID  # 0x090A

        # Simuler une réponse HID++ valide
        # Byte 0: REPORT_LONG (0x11), Byte 1: device_number (0xFF), Byte 2-3: request_id (0x090A), rest: payload
        response_payload = (
            b"\x02\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        response_message = (
            struct.pack("!BB", REPORT_LONG, device_number)
            + struct.pack("!H", resolved_request_id)
            + response_payload
        )
        transport.responses_to_read.append(response_message)

        reply = protocol.hidpp_request(transport, device_number, request_id, 0x01)

        # Vérifier que le message a été correctement construit et écrit
        self.assertEqual(len(transport.written_messages), 1)
        written = transport.written_messages[0]
        self.assertEqual(written[0], REPORT_LONG)
        self.assertEqual(written[1], device_number)
        self.assertEqual(written[2:4], struct.pack("!H", resolved_request_id))

        # Vérifier que le payload retourné est correct
        self.assertIsNotNone(reply)
        # hidpp_request retourne received_data[2:], ce qui exclut l'ID de requête (2 bytes) mais garde le reste du payload
        self.assertEqual(reply, response_payload)

    def test_hidpp_request_error_1_0(self):
        transport = MockTransport()
        device_number = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID  # 0x090A

        # Erreur HID++ 1.0 : REPORT_SHORT (0x10), device_number (0xFF), sub ID (0x8F), request_id (0x090A)
        error_message = (
            struct.pack("!BB", 0x10, device_number)
            + b"\x8f"
            + struct.pack("!H", resolved_request_id)
            + b"\x00\x00"
        )
        transport.responses_to_read.append(error_message)

        reply = protocol.hidpp_request(transport, device_number, request_id, 0x01)
        self.assertIsNone(reply)

    def test_hidpp_request_error_2_0(self):
        transport = MockTransport()
        device_number = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID  # 0x090A

        # Erreur HID++ 2.0 : received_data[0] = 0xFF, received_data[1:3] = request_id
        error_message = (
            struct.pack("!BB", REPORT_LONG, device_number)
            + b"\xff"
            + struct.pack("!H", resolved_request_id)
            + (b"\x00" * 15)
        )
        transport.responses_to_read.append(error_message)

        reply = protocol.hidpp_request(transport, device_number, request_id, 0x01)
        self.assertIsNone(reply)


class TestDrainTransport(unittest.TestCase):
    def test_drain_stops_on_none(self):
        transport = MockTransport()
        transport.responses_to_read = [b"\x11\xff" + b"\x00" * 18, None]
        protocol._drain_transport(transport)
        self.assertEqual(len(transport.written_messages), 0)

    def test_drain_stops_after_max_reads(self):
        transport = MockTransport()
        transport.responses_to_read = [b"\x11\xff" + b"\x00" * 18] * 100
        protocol._drain_transport(transport, max_reads=5)
        self.assertEqual(len(transport.written_messages), 0)


class TestSendChangeHost(unittest.TestCase):
    def _make_response(self, device_number, feature_index, host):
        from swigi.constants import CHANGE_HOST_FN_SET, SW_ID

        request_id = (feature_index << 8) | (CHANGE_HOST_FN_SET & 0xF0) | SW_ID
        return (
            struct.pack("!BB", REPORT_LONG, device_number)
            + struct.pack("!H", request_id)
            + b"\x00" * 16
        )

    def test_send_writes_5_times(self):
        transport = MockTransport()
        protocol.send_change_host(transport, 0xFF, 0x09, 1)
        self.assertEqual(len(transport.written_messages), 5)

    def test_send_all_writes_same_message(self):
        transport = MockTransport()
        protocol.send_change_host(transport, 0xFF, 0x09, 2)
        messages = transport.written_messages
        self.assertTrue(all(message == messages[0] for message in messages))

    def test_send_first_write_failure_raises(self):
        from swigi.transport import TransportError

        class FailFirstTransport(MockTransport):
            def write(self, message):
                raise TransportError("dead")

        fail_transport = FailFirstTransport()
        with self.assertRaises(TransportError):
            protocol.send_change_host(fail_transport, 0xFF, 0x09, 0)

    def test_send_retry_failure_is_success(self):
        from swigi.transport import TransportError

        call_count = [0]

        class FailAfterFirstTransport(MockTransport):
            def write(self, message):
                call_count[0] += 1
                if call_count[0] > 1:
                    raise TransportError("disconnected — switch succeeded")

        fail_transport = FailAfterFirstTransport()
        protocol.send_change_host(fail_transport, 0xFF, 0x09, 0)
        self.assertEqual(call_count[0], 2)


class TestResolveFeature(unittest.TestCase):
    def test_resolve_feature_found(self):
        transport = MockTransport()
        from swigi.constants import FEATURE_ROOT, SW_ID, REPORT_LONG

        request_id = (FEATURE_ROOT << 8) | 0x00 | SW_ID
        response = (
            struct.pack("!BB", REPORT_LONG, 0xFF)
            + struct.pack("!H", request_id)
            + bytes([0x05])
            + b"\x00" * 15
        )
        transport.responses_to_read.append(response)
        result = protocol.resolve_feature(transport, 0xFF, 0x0005)
        self.assertEqual(result, 0x05)

    def test_resolve_feature_not_found(self):
        transport = MockTransport()
        result = protocol.resolve_feature(transport, 0xFF, 0x9999)
        self.assertIsNone(result)


class TestGetCurrentHost(unittest.TestCase):
    def test_get_current_host(self):
        transport = MockTransport()
        feature_index = 0x09
        request_id = (feature_index << 8) | 0x00 | SW_ID
        response = (
            struct.pack("!BB", REPORT_LONG, 0xFF)
            + struct.pack("!H", request_id)
            + bytes([3, 1])
            + b"\x00" * 14
        )
        transport.responses_to_read.append(response)
        host = protocol.get_current_host(transport, 0xFF, feature_index)
        self.assertEqual(host, 1)

    def test_get_current_host_no_reply(self):
        transport = MockTransport()
        result = protocol.get_current_host(transport, 0xFF, 0x09)
        self.assertIsNone(result)

    def test_get_current_host_zero_is_valid(self):
        """currentHost=0 est une valeur valide (falsy en Python mais pas None)."""
        transport = MockTransport()
        feature_index = 0x09
        request_id = (feature_index << 8) | 0x00 | SW_ID
        # numHosts=3, currentHost=0 → valide
        response = (
            struct.pack("!BB", REPORT_LONG, 0xFF)
            + struct.pack("!H", request_id)
            + bytes([3, 0])
            + b"\x00" * 14
        )
        transport.responses_to_read.append(response)
        host = protocol.get_current_host(transport, 0xFF, feature_index)
        self.assertEqual(host, 0)
        self.assertIsNotNone(host)

    def test_get_current_host_invalid_range_returns_none(self):
        """currentHost >= numHosts → invalide → None."""
        transport = MockTransport()
        feature_index = 0x09
        request_id = (feature_index << 8) | 0x00 | SW_ID
        # numHosts=3, currentHost=5 → invalide
        response = (
            struct.pack("!BB", REPORT_LONG, 0xFF)
            + struct.pack("!H", request_id)
            + bytes([3, 5])
            + b"\x00" * 14
        )
        transport.responses_to_read.append(response)
        host = protocol.get_current_host(transport, 0xFF, feature_index)
        self.assertIsNone(host)


class TestHidppRequestPaddedResponse(unittest.TestCase):
    def test_hidpp_request_accepts_padded_32byte_response(self):
        """Réponse paddée à 32 octets doit être acceptée (macOS BT quirk)."""
        transport = MockTransport()
        device_number = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID
        # Response paddée à 32 bytes (MAX_READ_SIZE) au lieu de 20
        response_payload = bytes([0x01, 0x02]) + b"\x00" * 10
        response_message = (
            struct.pack("!BB", REPORT_LONG, device_number)
            + struct.pack("!H", resolved_request_id)
            + response_payload
            + b"\x00" * 12  # padding jusqu'à 32 bytes
        )
        transport.responses_to_read.append(response_message)
        reply = protocol.hidpp_request(transport, device_number, request_id)
        self.assertIsNotNone(reply)


class TestGetDeviceName(unittest.TestCase):
    def test_get_device_name_no_infinite_loop_on_malformed_device(self):
        """get_device_name ne boucle pas indéfiniment si name_len == 0 ou très grand."""
        transport = MagicMock()
        # Simuler la réponse à la requête 0x00 (get count) avec name_len=0
        with patch(
            "swigi.protocol.hidpp_request", return_value=b"\x00" + b"\x00" * 15
        ) as mock_req:
            result = get_device_name(transport, 0xFF, 0x04)
        self.assertIsNone(result)


class TestBuildMessageValidation(unittest.TestCase):
    def test_build_message_raises_on_oversized_parameters(self):
        """_build_message lève ValueError si parameters > 16 bytes."""
        from swigi.protocol import _build_message

        with self.assertRaises(ValueError):
            _build_message(0xFF, 0x0411, b"\x01" * 17)  # 17 bytes > 16 max

    def test_build_message_accepts_max_parameters(self):
        """_build_message accepte exactement 16 bytes de parameters."""
        from swigi.protocol import _build_message

        result = _build_message(0xFF, 0x0411, b"\x01" * 16)
        self.assertEqual(len(result), 20)  # 1 (report_id) + 1 (device) + 18 (data) = 20


if __name__ == "__main__":
    unittest.main()
