import unittest
from swigi import constants


class TestConstants(unittest.TestCase):
    def test_logitech_vid(self):
        self.assertEqual(constants.LOGITECH_VID, 0x046D)

    def test_receiver_pids(self):
        self.assertEqual(constants.BOLT_PID, 0xC548)
        self.assertIn(0xC52B, constants.UNIFYING_PIDS)
        self.assertIn(0xC532, constants.UNIFYING_PIDS)
        self.assertIn(constants.BOLT_PID, constants.ALL_RECEIVER_PIDS)

    def test_report_types(self):
        self.assertEqual(constants.REPORT_SHORT, 0x10)
        self.assertEqual(constants.REPORT_LONG, 0x11)
        self.assertEqual(constants.MSG_SHORT_LEN, 7)
        self.assertEqual(constants.MSG_LONG_LEN, 20)

    def test_msg_lengths_mapping(self):
        self.assertEqual(constants.MSG_LENGTHS[constants.REPORT_SHORT], constants.MSG_SHORT_LEN)
        self.assertEqual(constants.MSG_LENGTHS[constants.REPORT_LONG], constants.MSG_LONG_LEN)


if __name__ == "__main__":
    unittest.main()
