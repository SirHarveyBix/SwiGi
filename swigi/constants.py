import os
import platform
import struct

# ═══════════════════════════════════════════════════════════════════════════════
#  Constantes HID++
# ═══════════════════════════════════════════════════════════════════════════════

LOGITECH_VID = 0x046D

BOLT_PID = 0xC548
UNIFYING_PIDS = (0xC52B, 0xC532)
ALL_RECEIVER_PIDS = (BOLT_PID,) + UNIFYING_PIDS

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
MSG_SHORT_LEN = 7
MSG_LONG_LEN = 20
MAX_READ_SIZE = 32

FEATURE_ROOT = 0x0000
FEATURE_DEVICE_TYPE_AND_NAME = 0x0005
FEATURE_CHANGE_HOST = 0x1814

DEVICE_TYPE_KEYBOARD = 0
DEVICE_TYPE_MOUSE = 3
DEVICE_TYPE_TRACKPAD = 4
DEVICE_TYPE_TRACKBALL = 5

DEVICE_NUMBER_DIRECT = 0xFF
SW_ID = 0x0A  # identifiant SwiGi (CleverSwitch utilise 0x08)
CHANGE_HOST_FN_SET = 0x10  # = REPORT_SHORT numériquement, mais sémantique HID++ CHANGE_HOST fn SET

MSG_LENGTHS = {REPORT_SHORT: MSG_SHORT_LEN, REPORT_LONG: MSG_LONG_LEN}

# Paires Usage : HID++ fabricant + Generic Desktop (macOS BT n'expose que Generic Desktop)
DIRECT_USAGE_PAIRS = [
    (0xFF00, 0x0002),
    (0xFF43, 0x0202),
    (0xFF0C, 0x0001),
    (0x0001, 0x0006),  # Clavier
    (0x0001, 0x0002),  # Souris
]

SYSTEM = platform.system()

PREFS_FILE = os.path.expanduser("~/.swigi_prefs.json")

PING_REQUEST_ID = (FEATURE_ROOT << 8) | 0x00 | SW_ID
# Construction explicite du ping : 20 octets au total (format HID++ REPORT_LONG)
_PING_DATA = struct.pack("!H", PING_REQUEST_ID) + b"\x00" * 16  # 18 octets total
PING_MESSAGE = struct.pack("!BB", REPORT_LONG, DEVICE_NUMBER_DIRECT) + _PING_DATA
