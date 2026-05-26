from __future__ import annotations

import logging
import struct
import time

from swigi.constants import (
    CHANGE_HOST_FN_SET,
    FEATURE_ROOT,
    MSG_LENGTHS,
    REPORT_LONG,
    REPORT_SHORT,
    SW_ID,
)
from swigi.transport import HIDTransport, TransportError


log = logging.getLogger("swigi.protocol")


def _build_message(device_number: int, request_id: int, parameters: bytes) -> bytes:
    if len(parameters) > 16:
        raise ValueError(f"parameters trop longs : {len(parameters)} bytes (max 16)")
    data = struct.pack("!H", request_id) + parameters
    return struct.pack("!BB18s", REPORT_LONG, device_number, data)


def _pack_parameters(parameters: list | tuple) -> bytes:
    parts = []
    for parameter in parameters:
        if isinstance(parameter, int):
            parts.append(struct.pack("B", parameter))
        else:
            parts.append(bytes(parameter))
    return b"".join(parts)


def hidpp_request(
    transport: HIDTransport,
    device_number: int,
    request_id: int,
    *parameters,
    timeout: int = 500,
) -> bytes | None:
    """Envoie une requête HID++ et retourne le contenu de la réponse, ou None."""
    request_id = (request_id & 0xFFF0) | SW_ID
    parameters_bytes = _pack_parameters(parameters) if parameters else b""
    request_data = struct.pack("!H", request_id) + parameters_bytes
    message = _build_message(device_number, request_id, parameters_bytes)

    transport.write(message)

    deadline = time.time() + timeout / 1000
    while True:
        now = time.time()
        if now >= deadline:
            break
        remaining_ms = max(1, int((deadline - now) * 1000))
        raw_bytes = transport.read(min(timeout, remaining_ms))
        if not raw_bytes or len(raw_bytes) < 4:
            continue
        if (
            raw_bytes[0] not in MSG_LENGTHS
            or len(raw_bytes) < MSG_LENGTHS[raw_bytes[0]]
        ):
            continue

        received_device = raw_bytes[1]
        if received_device != device_number and received_device != (
            device_number ^ 0xFF
        ):
            continue

        received_data = raw_bytes[2:]

        # Erreur HID++ 1.0
        if (
            raw_bytes[0] == REPORT_SHORT
            and received_data[0:1] == b"\x8f"
            and received_data[1:3] == request_data[:2]
        ):
            return None
        # Erreur HID++ 2.0
        if received_data[0:1] == b"\xff" and received_data[1:3] == request_data[:2]:
            return None
        # Succès
        if received_data[:2] == request_data[:2]:
            return received_data[2:]

    return None


def resolve_feature(
    transport: HIDTransport, device_number: int, feature_code: int
) -> int | None:
    """Recherche l'index de feature. Retourne l'index ou None."""
    request_id = (FEATURE_ROOT << 8) | 0x00
    reply = hidpp_request(
        transport,
        device_number,
        request_id,
        feature_code >> 8,
        feature_code & 0xFF,
        0x00,
        timeout=500,
    )
    if reply and reply[0] != 0x00:
        return reply[0]
    return None


def get_device_type(
    transport: HIDTransport, device_number: int, feature_index: int
) -> int | None:
    reply = hidpp_request(
        transport, device_number, (feature_index << 8) | 0x20, timeout=500
    )
    return reply[0] if reply else None


def get_device_name(
    transport: HIDTransport, device_number: int, feature_index: int
) -> str | None:
    reply = hidpp_request(
        transport, device_number, (feature_index << 8) | 0x00, timeout=500
    )
    if not reply:
        return None
    name_len = min(reply[0], 64)
    if name_len == 0:
        return None
    chars = []
    while len(chars) < name_len:
        reply = hidpp_request(
            transport,
            device_number,
            (feature_index << 8) | 0x10,
            len(chars),
            timeout=500,
        )
        if not reply:
            break
        to_read = name_len - len(chars)
        if to_read <= 0:
            break
        chars.extend(reply[:to_read])
    return bytes(chars).decode("utf-8", errors="replace") if chars else None


def _drain_transport(transport: HIDTransport, max_reads: int = 32) -> None:
    """Vide le buffer d'entrée HID avant d'écrire une commande.

    timeout=1 (1ms) plutôt que 0 : sur macOS Sonoma/Sequoia + BT 5.3 (M3),
    hid_read_timeout(..., 0) peut ignorer des paquets déjà en file kernel.
    1ms suffit pour que le BT stack rende les paquets disponibles.
    """
    for _ in range(max_reads):
        try:
            if transport.read(timeout=1) is None:
                break
        except (TransportError, OSError):
            break


def send_change_host(
    transport: HIDTransport, device_number: int, feature_index: int, target_host: int
) -> None:
    """Bascule le périphérique vers target_host (base 0).

    Double drain (avant + après 1ms d'attente) pour absorber les paquets
    in-flight sur les chips BT 5.3 haute fréquence (M3 Pro).
    Envoie la commande 5× back-to-back sans délai.
    Exception sur 1er essai = erreur réelle (propagée).
    Exception sur retry = périphérique déconnecté après switch réussi (ignorée).
    """
    _drain_transport(transport)
    time.sleep(0.001)  # laisse arriver les paquets BT in-flight
    _drain_transport(transport)

    request_id = (feature_index << 8) | (CHANGE_HOST_FN_SET & 0xF0) | SW_ID
    parameters = struct.pack("B", target_host)
    message = _build_message(device_number, request_id, parameters)
    for attempt in range(5):
        try:
            transport.write(message)
        except (TransportError, OSError):
            if attempt == 0:
                raise  # 1er essai échoué = transport mort avant envoi
            return  # retry échoué = switch réussi, périphérique déconnecté
    # Flush OS TX buffer : lecture courte force le BT stack à expédier les writes en attente
    try:
        transport.read(timeout=10)
    except (TransportError, OSError):
        pass  # souris déconnectée = commande reçue, comportement attendu


def get_current_host(
    transport: HIDTransport, device_number: int, feature_index: int
) -> int | None:
    """Interroge CHANGE_HOST getHostInfo (fn 0). Retourne l'hôte actuel (base 0) ou None."""
    reply = hidpp_request(
        transport, device_number, (feature_index << 8) | 0x00, timeout=500
    )
    if reply and len(reply) >= 2:
        num_hosts, current_host = reply[0], reply[1]
        if num_hosts > 0 and 0 <= current_host < num_hosts:
            return current_host
    return None
