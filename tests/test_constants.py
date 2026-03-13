import unittest

from swigi import constants


class TestConstants(unittest.TestCase):
    """Vérifie l'intégrité des constantes HID++ et des messages pré-construits."""

    def test_logitech_vid(self):
        """VID Logitech correct."""
        self.assertEqual(constants.LOGITECH_VID, 0x046D)

    def test_receiver_pids(self):
        """PIDs des receivers Bolt et Unifying présents."""
        self.assertEqual(constants.BOLT_PID, 0xC548)
        self.assertIn(0xC52B, constants.UNIFYING_PIDS)
        self.assertIn(0xC532, constants.UNIFYING_PIDS)
        self.assertIn(constants.BOLT_PID, constants.ALL_RECEIVER_PIDS)

    def test_report_types(self):
        """Report IDs et longueurs HID++ correctement définis."""
        self.assertEqual(constants.REPORT_SHORT, 0x10)
        self.assertEqual(constants.REPORT_LONG, 0x11)
        self.assertEqual(constants.MSG_SHORT_LEN, 7)
        self.assertEqual(constants.MSG_LONG_LEN, 20)

    def test_msg_lengths_mapping(self):
        """MSG_LENGTHS mappe les report IDs vers les bonnes longueurs."""
        self.assertEqual(
            constants.MSG_LENGTHS[constants.REPORT_SHORT], constants.MSG_SHORT_LEN
        )
        self.assertEqual(
            constants.MSG_LENGTHS[constants.REPORT_LONG], constants.MSG_LONG_LEN
        )

    def test_ping_msg_format(self):
        """PING_MESSAGE fait 20 octets avec les bons headers."""
        self.assertEqual(len(constants.PING_MESSAGE), 20)
        self.assertEqual(constants.PING_MESSAGE[0], constants.REPORT_LONG)
        self.assertEqual(constants.PING_MESSAGE[1], constants.DEVICE_NUMBER_DIRECT)


if __name__ == "__main__":
    unittest.main()
