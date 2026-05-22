import dataclasses
import logging
from swigi.constants import (
    ALL_RECEIVER_PIDS,
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    DEVICE_TYPE_TRACKBALL,
    DEVICE_TYPE_TRACKPAD,
    DEVNUMBER_DIRECT,
    DIRECT_USAGE_PAIRS,
    FEATURE_CHANGE_HOST,
    FEATURE_DEVICE_TYPE_AND_NAME,
    LOGITECH_VID,
)
from swigi.hidapi_loader import DeviceInfoStruct, lib
from swigi.protocol import get_device_name, get_device_type, resolve_feature
from swigi.transport import HIDTransport, TransportError

log = logging.getLogger("swigi.discovery")


@dataclasses.dataclass
class DeviceInfo:
    transport: HIDTransport
    name: str
    pid: int
    change_host_idx: int

    def close(self):
        try:
            self.transport.close()
        except Exception:
            pass


def find_device(device_type_wanted: int) -> DeviceInfo | None:
    """Cherche périphérique Logitech BT. 0=clavier, 3=souris, 4=trackpad, 5=trackball."""
    head = lib.hid_enumerate(LOGITECH_VID, 0)
    candidates = []
    node = head
    while node:
        info = node.contents
        node = info.next
        pid = info.product_id
        up = info.usage_page
        usage = info.usage
        if pid in ALL_RECEIVER_PIDS:
            continue
        if (up, usage) not in DIRECT_USAGE_PAIRS:
            continue
        score = 100 if up in (0xFF00, 0xFF43, 0xFF0C) else 0
        candidates.append((score, info.path, pid, up, usage))
    lib.hid_free_enumeration(head)
    candidates.sort(key=lambda x: -x[0])

    for score, path, pid, up, usage in candidates:
        try:
            t = HIDTransport(path, pid)
        except OSError:
            log.debug("Ouverture échouée pid=0x%04X up=0x%04X u=0x%04X", pid, up, usage)
            continue
        try:
            feat = resolve_feature(t, DEVNUMBER_DIRECT, FEATURE_DEVICE_TYPE_AND_NAME)
            if feat is None:
                t.close()
                continue
            dt = get_device_type(t, DEVNUMBER_DIRECT, feat)
            name = get_device_name(t, DEVNUMBER_DIRECT, feat) or f"Logitech-0x{pid:04X}"
            is_mouse = dt in (DEVICE_TYPE_MOUSE, DEVICE_TYPE_TRACKPAD, DEVICE_TYPE_TRACKBALL)
            if device_type_wanted == DEVICE_TYPE_KEYBOARD and dt != DEVICE_TYPE_KEYBOARD:
                t.close()
                continue
            if device_type_wanted == DEVICE_TYPE_MOUSE and not is_mouse:
                t.close()
                continue
            ch = resolve_feature(t, DEVNUMBER_DIRECT, FEATURE_CHANGE_HOST)
            if ch is None:
                t.close()
                continue
            return DeviceInfo(t, name, pid, ch)
        except (TransportError, OSError):
            t.close()
            continue
    return None
