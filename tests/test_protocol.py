import struct
import unittest
from unittest.mock import MagicMock

from swigi import protocol
from swigi.constants import REPORT_LONG, SW_ID


class MockTransport:
    def __init__(self):
        self.written_messages = []
        self.responses_to_read = []
        self.is_open = True

    def write(self, msg: bytes) -> None:
        self.written_messages.append(msg)

    def read(self, timeout: int = 500) -> bytes | None:
        if self.responses_to_read:
            return self.responses_to_read.pop(0)
        return None

    def close(self) -> None:
        self.is_open = False


class TestProtocol(unittest.TestCase):
    def test_build_msg(self):
        devnumber = 0xFF
        request_id = 0x090A
        params = b"\x00\x01\x02"

        msg = protocol._build_msg(devnumber, request_id, params)

        self.assertEqual(len(msg), 20)
        self.assertEqual(msg[0], REPORT_LONG)
        self.assertEqual(msg[1], devnumber)
        self.assertEqual(msg[2:4], struct.pack("!H", request_id))
        self.assertEqual(msg[4:7], params)
        # padding should be zero
        self.assertEqual(msg[7:], b"\x00" * 13)

    def test_pack_params(self):
        params = [0x01, b"\x02\x03", 0x04]
        packed = protocol._pack_params(params)
        self.assertEqual(packed, b"\x01\x02\x03\x04")

    def test_hidpp_request_success(self):
        transport = MockTransport()
        devnumber = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID  # 0x090A

        # Simuler une réponse HID++ valide
        # Byte 0: REPORT_LONG (0x11), Byte 1: devnumber (0xFF), Byte 2-3: request_id (0x090A), rest: payload
        response_payload = b"\x02\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        response_msg = struct.pack("!BB", REPORT_LONG, devnumber) + struct.pack("!H", resolved_request_id) + response_payload
        transport.responses_to_read.append(response_msg)

        reply = protocol.hidpp_request(transport, devnumber, request_id, 0x01)

        # Vérifier que le message a été correctement construit et écrit
        self.assertEqual(len(transport.written_messages), 1)
        written = transport.written_messages[0]
        self.assertEqual(written[0], REPORT_LONG)
        self.assertEqual(written[1], devnumber)
        self.assertEqual(written[2:4], struct.pack("!H", resolved_request_id))

        # Vérifier que le payload retourné est correct
        self.assertIsNotNone(reply)
        # hidpp_request retourne rdata[2:], ce qui exclut l'ID de requête (2 bytes) mais garde le reste du payload
        self.assertEqual(reply, response_payload)

    def test_hidpp_request_error_1_0(self):
        transport = MockTransport()
        devnumber = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID  # 0x090A

        # Erreur HID++ 1.0 : REPORT_SHORT (0x10), devnumber (0xFF), sub ID (0x8F), request_id (0x090A)
        # structure de raw[2:] dans REPORT_SHORT : rdata[0] = 0x8F, rdata[1:3] = request_id
        error_msg = struct.pack("!BB", 0x10, devnumber) + b"\x8f" + struct.pack("!H", resolved_request_id) + b"\x00\x00"
        transport.responses_to_read.append(error_msg)

        reply = protocol.hidpp_request(transport, devnumber, request_id, 0x01)
        self.assertIsNone(reply)

    def test_hidpp_request_error_2_0(self):
        transport = MockTransport()
        devnumber = 0xFF
        request_id = 0x0900
        resolved_request_id = (request_id & 0xFFF0) | SW_ID  # 0x090A

        # Erreur HID++ 2.0 : rdata[0] = 0xFF, rdata[1:3] = request_id
        error_msg = struct.pack("!BB", REPORT_LONG, devnumber) + b"\xff" + struct.pack("!H", resolved_request_id) + (b"\x00" * 15)
        transport.responses_to_read.append(error_msg)

        reply = protocol.hidpp_request(transport, devnumber, request_id, 0x01)
        self.assertIsNone(reply)


if __name__ == "__main__":
    unittest.main()
