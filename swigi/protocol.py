from __future__ import annotations

import logging
import struct
import time
from typing import TYPE_CHECKING

from swigi.constants import (
    CHANGE_HOST_FN_SET,
    DEVNUMBER_DIRECT,
    FEATURE_ROOT,
    MSG_LENGTHS,
    REPORT_LONG,
    REPORT_SHORT,
    SW_ID,
)
from swigi.transport import HIDTransport, TransportError

if TYPE_CHECKING:
    from swigi.discovery import DeviceInfo

log = logging.getLogger("swigi.protocol")


def _build_msg(devnumber: int, request_id: int, params: bytes) -> bytes:
    data = struct.pack("!H", request_id) + params
    return struct.pack("!BB18s", REPORT_LONG, devnumber, data)


def _pack_params(params: list | tuple) -> bytes:
    parts = []
    for p in params:
        if isinstance(p, int):
            parts.append(struct.pack("B", p))
        else:
            parts.append(bytes(p))
    return b"".join(parts)


def hidpp_request(
    transport: HIDTransport,
    devnumber: int,
    request_id: int,
    *params,
    timeout: int = 500,
) -> bytes | None:
    """Envoie une requête HID++ et retourne le contenu de la réponse, ou None."""
    request_id = (request_id & 0xFFF0) | SW_ID
    params_bytes = _pack_params(params) if params else b""
    request_data = struct.pack("!H", request_id) + params_bytes
    msg = _build_msg(devnumber, request_id, params_bytes)

    transport.write(msg)

    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        remaining_ms = max(1, int((deadline - time.time()) * 1000))
        raw = transport.read(min(timeout, remaining_ms))
        if not raw or len(raw) < 4:
            continue
        if raw[0] not in MSG_LENGTHS or len(raw) != MSG_LENGTHS[raw[0]]:
            continue

        rdev = raw[1]
        if rdev != devnumber and rdev != (devnumber ^ 0xFF):
            continue

        rdata = raw[2:]

        # Erreur HID++ 1.0
        if raw[0] == REPORT_SHORT and rdata[0:1] == b"\x8f" and rdata[1:3] == request_data[:2]:
            return None
        # Erreur HID++ 2.0
        if rdata[0:1] == b"\xff" and rdata[1:3] == request_data[:2]:
            return None
        # Succès
        if rdata[:2] == request_data[:2]:
            return rdata[2:]

    return None


def resolve_feature(transport: HIDTransport, devnumber: int, feature_code: int) -> int | None:
    """Recherche l'index de feature. Retourne l'index ou None."""
    request_id = (FEATURE_ROOT << 8) | 0x00
    reply = hidpp_request(
        transport,
        devnumber,
        request_id,
        feature_code >> 8,
        feature_code & 0xFF,
        0x00,
        timeout=500,
    )
    if reply and reply[0] != 0x00:
        return reply[0]
    return None


def get_device_type(transport: HIDTransport, devnumber: int, feat_idx: int) -> int | None:
    reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x20, timeout=500)
    return reply[0] if reply else None


def get_device_name(transport: HIDTransport, devnumber: int, feat_idx: int) -> str | None:
    reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x00, timeout=500)
    if not reply:
        return None
    name_len = min(reply[0], 64)
    if name_len == 0:
        return None
    chars = []
    while len(chars) < name_len:
        reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x10, len(chars), timeout=500)
        if not reply:
            break
        chars.extend(reply[: name_len - len(chars)])
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


def send_change_host(transport: HIDTransport, devnumber: int, feat_idx: int, target_host: int) -> None:
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

    request_id = (feat_idx << 8) | (CHANGE_HOST_FN_SET & 0xF0) | SW_ID
    params = struct.pack("B", target_host)
    msg = _build_msg(devnumber, request_id, params)
    for attempt in range(5):
        try:
            transport.write(msg)
        except (TransportError, OSError):
            if attempt == 0:
                raise  # 1er essai échoué = transport mort avant envoi
            return  # retry échoué = switch réussi, périphérique déconnecté
    # Flush OS TX buffer : lecture courte force le BT stack à expédier les writes en attente
    try:
        transport.read(timeout=10)
    except (TransportError, OSError):
        pass  # souris déconnectée = commande reçue, comportement attendu


def get_current_host(transport: HIDTransport, devnumber: int, feat_idx: int) -> int | None:
    """Interroge CHANGE_HOST getHostInfo (fn 0). Retourne l'hôte actuel (base 0) ou None."""
    reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x00, timeout=500)
    if reply and len(reply) >= 2:
        # reply[0] = numHosts, reply[1] = currentHost
        return reply[1]
    return None


def _verify_and_sync(kb: DeviceInfo, mouse: DeviceInfo, state: dict) -> None:
    """Vérifie que clavier et souris sont sur le même hôte. Corrige si désynchronisé.

    Appelé après reconnexion des deux périphériques. Si les hôtes diffèrent,
    envoie CHANGE_HOST à la souris pour la ramener sur l'hôte du clavier.
    """
    try:
        kb_host = get_current_host(kb.transport, DEVNUMBER_DIRECT, kb.change_host_idx)
        mouse_host = get_current_host(mouse.transport, DEVNUMBER_DIRECT, mouse.change_host_idx)
    except (TransportError, OSError) as e:
        log.warning("Impossible de lire l'hôte actuel pour vérification (erreur transport) : %s", e)
        return

    if kb_host is None or mouse_host is None:
        return  # impossible de vérifier

    if kb_host == mouse_host:
        log.debug("Sync OK : clavier et souris sur hôte %d", kb_host)
        return

    log.warning(
        "Désync détectée : clavier=hôte%d, souris=hôte%d → correction...",
        kb_host,
        mouse_host,
    )

    from swigi.gui import notify

    notify(f"Resynchronisation → hôte {kb_host + 1}", "SwiGi")
    try:
        send_change_host(mouse.transport, DEVNUMBER_DIRECT, mouse.change_host_idx, kb_host)
        log.info("Correction appliquée : souris → hôte %d", kb_host)
        mouse.close()
        state["mouse"] = None  # souris va déconnecter après la correction
    except (TransportError, OSError) as e:
        log.warning("Correction sync échouée : %s", e)
        mouse.close()
        state["mouse"] = None
