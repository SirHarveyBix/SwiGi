import dataclasses
import logging

from swigi.constants import (
    ALL_RECEIVER_PIDS,
    DEVICE_NUMBER_DIRECT,
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    DEVICE_TYPE_TRACKBALL,
    DEVICE_TYPE_TRACKPAD,
    DIRECT_USAGE_PAIRS,
    FEATURE_BACKLIGHT2,
    FEATURE_CHANGE_HOST,
    FEATURE_DEVICE_TYPE_AND_NAME,
    LOGITECH_VID,
)
from swigi.hidapi_loader import lib
from swigi.protocol import (
    drain_transport,
    get_device_name,
    get_device_type,
    get_protocol_version,
    resolve_feature,
)
from swigi.transport import HIDTransport, TransportError

log = logging.getLogger("swigi.discovery")

# ── Device discovery ──────────────────────────────────────────────────────────


@dataclasses.dataclass
class DeviceInfo:
    transport: HIDTransport
    name: str
    product_id: int
    change_host_index: int
    push_capable: bool = True
    backlight_index: int | None = None

    def close(self):
        try:
            self.transport.close()
        except (OSError, TransportError):
            pass


def _clean_name(raw_name: str | None, product_id: int) -> str:
    """Filtre les null bytes et espaces parasites du nom HID.

    Retourne un fallback lisible si le nom est vide après nettoyage.
    """
    if not raw_name:
        return f"Logitech-0x{product_id:04X}"
    cleaned = raw_name.replace("\x00", "").strip()
    return cleaned if cleaned else f"Logitech-0x{product_id:04X}"


def find_all_devices(device_type_wanted: int) -> list[DeviceInfo]:
    """Retourne TOUS les périphériques Logitech BT du type voulu.

    0=clavier, 3=souris, 4=trackpad, 5=trackball.
    Contrairement à find_device, ne s'arrête pas au premier résultat.
    """
    enumeration_head = lib.hid_enumerate(LOGITECH_VID, 0)
    candidates = []
    enumeration_node = enumeration_head
    while enumeration_node:
        device_info = enumeration_node.contents
        enumeration_node = device_info.next
        product_id = device_info.product_id
        usage_page = device_info.usage_page
        usage = device_info.usage
        if product_id in ALL_RECEIVER_PIDS:
            continue
        if (usage_page, usage) not in DIRECT_USAGE_PAIRS:
            continue
        compatibility_score = 100 if usage_page in (0xFF00, 0xFF43, 0xFF0C) else 0
        candidates.append(
            (compatibility_score, device_info.path, product_id, usage_page, usage)
        )
    lib.hid_free_enumeration(enumeration_head)
    candidates.sort(key=lambda x: -x[0])

    seen_product_ids: set[int] = set()
    results = []
    for compatibility_score, path, product_id, usage_page, usage in candidates:
        # Un seul handle par PID — un périphérique BT expose plusieurs interfaces HID
        # (keyboard HID, vendor HID++, etc.) avec le même PID. Ouvrir les deux →
        # double-free dans libhidapi → malloc crash.
        if product_id in seen_product_ids:
            log.debug("PID=0x%04X déjà traité (interface multiple ignorée)", product_id)
            continue
        transport = None
        try:
            transport = HIDTransport(path, product_id)
            # Vide le buffer kernel avant toute requête HID++ — évite de lire une réponse
            # stale d'une session précédente (cause du nom corrompu après reconnexion BT).
            drain_transport(transport)
            feature_index = resolve_feature(
                transport, DEVICE_NUMBER_DIRECT, FEATURE_DEVICE_TYPE_AND_NAME
            )
            if feature_index is None:
                transport.close()
                transport = None
                continue
            device_type = get_device_type(
                transport, DEVICE_NUMBER_DIRECT, feature_index
            )
            raw_name = get_device_name(transport, DEVICE_NUMBER_DIRECT, feature_index)
            name = _clean_name(raw_name, product_id)
            is_mouse = device_type in (
                DEVICE_TYPE_MOUSE,
                DEVICE_TYPE_TRACKPAD,
                DEVICE_TYPE_TRACKBALL,
            )
            if (
                device_type_wanted == DEVICE_TYPE_KEYBOARD
                and device_type != DEVICE_TYPE_KEYBOARD
            ):
                transport.close()
                transport = None
                continue
            if device_type_wanted == DEVICE_TYPE_MOUSE and not is_mouse:
                transport.close()
                transport = None
                continue

            # Important: après les lectures de nom/type, des réponses HID++ peuvent encore
            # traîner dans le buffer. Sans ce drain, resolve_feature(CHANGE_HOST) peut lire
            # un paquet stale et retourner un index incohérent (désync/false switch).
            drain_transport(transport)
            change_host_index = resolve_feature(
                transport, DEVICE_NUMBER_DIRECT, FEATURE_CHANGE_HOST
            )
            if change_host_index == feature_index:
                # Rare sur reconnect BT instable : même index que DEVICE_TYPE_AND_NAME.
                # Retenter une fois ; si toujours incohérent, rejeter le device.
                drain_transport(transport)
                retry_index = resolve_feature(
                    transport, DEVICE_NUMBER_DIRECT, FEATURE_CHANGE_HOST
                )
                if retry_index is None or retry_index == feature_index:
                    transport.close()
                    transport = None
                    continue
                change_host_index = retry_index
            if change_host_index is None:
                log.warning(
                    "⚠️ [%s] PID=0x%04X — feature CHANGE_HOST absente (ancienne génération non supportée)",
                    name,
                    product_id,
                )
                transport.close()
                transport = None
                continue
            drain_transport(transport)
            proto = get_protocol_version(transport, DEVICE_NUMBER_DIRECT, timeout=200)
            push_capable = proto[0] >= 4 if proto is not None else True
            # BACKLIGHT2 uniquement sur les claviers — les souris n'ont pas de rétroéclairage
            backlight_index = None
            if device_type == DEVICE_TYPE_KEYBOARD:
                drain_transport(transport)
                backlight_index = resolve_feature(
                    transport, DEVICE_NUMBER_DIRECT, FEATURE_BACKLIGHT2
                )
                if backlight_index is None:
                    log.info("💡 [%s] PID=0x%04X — feature BACKLIGHT2 (0x8070) absente", name, product_id)
            seen_product_ids.add(product_id)
            results.append(
                DeviceInfo(transport, name, product_id, change_host_index, push_capable, backlight_index)
            )
            transport = None  # ownership transférée à DeviceInfo
        except (TransportError, OSError) as err:
            log.debug("PID=0x%04X ignoré — erreur HID : %s", product_id, err)
        finally:
            if transport is not None:
                transport.close()
    return results


def find_device(device_type_wanted: int) -> DeviceInfo | None:
    """Cherche périphérique Logitech BT. 0=clavier, 3=souris, 4=trackpad, 5=trackball.

    Retourne le premier résultat de find_all_devices — conservé pour compatibilité.
    """
    results = find_all_devices(device_type_wanted)
    return results[0] if results else None
