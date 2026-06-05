#!/usr/bin/env python3
"""Scan toutes les features HID++ des claviers connectés."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swigi.constants import DEVICE_NUMBER_DIRECT, DEVICE_TYPE_KEYBOARD
from swigi.discovery import find_all_devices
from swigi.protocol import hidpp_request, resolve_feature

FEATURE_SET = 0x0001

KNOWN_FEATURE_NAMES = {
    0x0000: "IRoot",
    0x0001: "IFeatureSet",
    0x0002: "IFirmwareInfo",
    0x0003: "GetDeviceUnitID",
    0x0005: "IDeviceTypeAndName",
    0x0007: "DeviceGroups",
    0x0020: "IConfigurableDeviceProperties",
    0x0040: "UnifiedBattery",
    0x1000: "BatteryStatus",
    0x1001: "BatteryVoltage",
    0x1004: "UnifiedBattery",
    0x1814: "ChangeHost",
    0x1815: "HostsInfo",
    0x1981: "Backlight",
    0x1982: "Backlight2",
    0x1983: "Backlight3",
    0x1990: "IlluminationLight",
    0x2100: "VerticalScrolling",
    0x2110: "SmartShift",
    0x2120: "FnInversion",
    0x2170: "KeyboardLayout",
    0x4100: "UnifiedScrolling",
    0x4220: "SpecialKeysMSEButtons",
    0x4521: "DisableKeysByUsage",
    0x4522: "PointerAxes",
    0x8010: "Gaming",
    0x8040: "BrightnessControl",
    0x8060: "AdjustableReportRate",
    0x8070: "Backlight2",
    0x8071: "Backlight2Extended",
    0x8080: "Illumination",
    0x8090: "IlluminationLight",
    0x8100: "OnboardProfiles",
    0x8110: "MouseButtonSpy",
    0x8300: "Sidetone",
    0x8600: "EqualizerFunction",
    0xF522: "DisableKeysByUsage",
    0xFF07: "GestureNavigation",
}


def scan(transport):
    feature_set_index = resolve_feature(transport, DEVICE_NUMBER_DIRECT, FEATURE_SET)
    if feature_set_index is None:
        print("  IFeatureSet (0x0001) introuvable")
        return

    reply = hidpp_request(transport, DEVICE_NUMBER_DIRECT, (feature_set_index << 8) | 0x00, timeout=300)
    if not reply:
        print("  getCount échoué")
        return
    count = reply[0]
    print(f"  IFeatureSet index={feature_set_index}, {count} features\n")

    for index in range(1, count + 1):
        feature_reply = hidpp_request(
            transport, DEVICE_NUMBER_DIRECT, (feature_set_index << 8) | 0x10, index, timeout=300
        )
        if not feature_reply or len(feature_reply) < 2:
            print(f"  [{index:3d}] ??? (lecture échouée)")
            continue
        feature_code = (feature_reply[0] << 8) | feature_reply[1]
        flags = feature_reply[2] if len(feature_reply) > 2 else 0
        tags = []
        if flags & 0x80:
            tags.append("OBSOLETE")
        if flags & 0x40:
            tags.append("HIDDEN")
        feature_name = KNOWN_FEATURE_NAMES.get(feature_code, "")
        tag_string = " ".join(tags)
        print(f"  [{index:3d}] 0x{feature_code:04X}  {feature_name:<30} {tag_string}")


def main():
    keyboards = find_all_devices(DEVICE_TYPE_KEYBOARD)
    if not keyboards:
        print("Aucun clavier trouvé — connecter le clavier en BT d'abord.")
        return
    for keyboard in keyboards:
        print(f"=== {keyboard.name}  0x{keyboard.product_id:04X}  CHANGE_HOST index={keyboard.change_host_index} ===")
        scan(keyboard.transport)
        keyboard.close()
        print()


if __name__ == "__main__":
    main()
