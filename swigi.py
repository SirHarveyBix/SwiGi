#!/usr/bin/env python3
"""SwiGi — synchronisation Easy-Switch via Bluetooth.

Quand Easy-Switch est pressé sur le clavier Logitech, capture la notification
CHANGE_HOST et envoie la même commande à la souris. Les deux basculent sur le même hôte.

Autonome : tout le code HID++ est inclus. Seule dépendance = bibliothèque hidapi.
Icône menu bar macOS : install_mac.sh installe rumps automatiquement.

macOS:  bash install_mac.sh  (installe tout + autostart)
Windows: hidapi.dll dans le dossier de ce fichier + double-cliquer setup_win.bat
Linux:  sudo apt install libhidapi-hidraw0 && python3 swigi.py

Options :
  python swigi.py        # mode normal
  python swigi.py -v     # verbose
  python swigi.py --log-file swigi.log
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import dataclasses
import json
import logging
import logging.handlers
import os
import platform
import signal
import struct
import subprocess
import sys
import threading
import time

log = logging.getLogger("swigi")

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

DEVNUMBER_DIRECT = 0xFF
SW_ID = 0x0A  # identifiant SwiGi (CleverSwitch utilise 0x08)
CHANGE_HOST_FN_SET = 0x10

_MSG_LENGTHS = {REPORT_SHORT: MSG_SHORT_LEN, REPORT_LONG: MSG_LONG_LEN}

# Paires Usage : HID++ fabricant + Generic Desktop (macOS BT n'expose que Generic Desktop)
DIRECT_USAGE_PAIRS = [
    (0xFF00, 0x0002), (0xFF43, 0x0202), (0xFF0C, 0x0001),
    (0x0001, 0x0006),  # Clavier
    (0x0001, 0x0002),  # Souris
]

# ═══════════════════════════════════════════════════════════════════════════════
#  Chargement hidapi
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM = platform.system()

# rumps : icône menu bar macOS (installé par install_mac.sh, optionnel)
try:
    import rumps as _rumps
    _HAS_RUMPS = _SYSTEM == "Darwin"
except ImportError:
    _rumps = None
    _HAS_RUMPS = False


class TransportError(Exception):
    pass


def _load_hidapi() -> ctypes.CDLL:
    """Charge hidapi. Ordre de recherche : répertoire app, bundle PyInstaller, système."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    meipass = getattr(sys, "_MEIPASS", None)  # PyInstaller

    search_dirs = [app_dir]
    if meipass:
        search_dirs.append(meipass)

    if _SYSTEM == "Darwin":
        local_names = ["libhidapi.dylib"]
        system_names = [
            "/opt/homebrew/lib/libhidapi.dylib",
            "/usr/local/lib/libhidapi.dylib",
            "libhidapi.dylib",
        ]
    elif _SYSTEM == "Windows":
        local_names = ["hidapi.dll", "libhidapi-0.dll"]
        system_names = ["hidapi.dll", "libhidapi-0.dll"]
        for d in search_dirs:
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
        scripts_dir = os.path.join(sys.prefix, "Scripts")
        if os.path.isdir(scripts_dir):
            try:
                os.add_dll_directory(scripts_dir)
            except Exception:
                pass
    else:  # Linux
        local_names = ["libhidapi-hidraw.so.0", "libhidapi-hidraw.so", "libhidapi.so.0", "libhidapi.so"]
        system_names = local_names + ["libhidapi-libusb.so.0", "libhidapi-libusb.so"]

    for d in search_dirs:
        for name in local_names:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                try:
                    lib = ctypes.CDLL(path)
                    log.debug("hidapi: chargé %s (local)", path)
                    return lib
                except OSError:
                    continue

    for name in system_names:
        try:
            lib = ctypes.CDLL(name)
            log.debug("hidapi: chargé %s (système)", name)
            return lib
        except OSError:
            continue

    hints = {
        "Darwin": "brew install hidapi  OU  copier libhidapi.dylib dans le dossier de ce fichier",
        "Windows": "Télécharger hidapi.dll depuis github.com/libusb/hidapi/releases",
        "Linux": "sudo apt install libhidapi-hidraw0",
    }
    raise ImportError(f"hidapi introuvable — {hints.get(_SYSTEM, 'installer hidapi')}")


_lib = _load_hidapi()

_lib.hid_init.restype = ctypes.c_int
_lib.hid_init.argtypes = []
_lib.hid_init()

# macOS : non-exclusif (coexiste avec Logi Options+)
if _SYSTEM == "Darwin":
    _fn = getattr(_lib, "hid_darwin_set_open_exclusive", None)
    if _fn:
        _fn.argtypes = [ctypes.c_int]
        _fn.restype = None
        _fn(0)

# ═══════════════════════════════════════════════════════════════════════════════
#  Liaisons hidapi
# ═══════════════════════════════════════════════════════════════════════════════


class _DeviceInfo(ctypes.Structure):
    pass


_DeviceInfo._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", ctypes.POINTER(_DeviceInfo)),
]

_lib.hid_enumerate.restype = ctypes.POINTER(_DeviceInfo)
_lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
_lib.hid_free_enumeration.restype = None
_lib.hid_free_enumeration.argtypes = [ctypes.POINTER(_DeviceInfo)]
_lib.hid_open_path.restype = ctypes.c_void_p
_lib.hid_open_path.argtypes = [ctypes.c_char_p]
_lib.hid_close.restype = None
_lib.hid_close.argtypes = [ctypes.c_void_p]
_lib.hid_read_timeout.restype = ctypes.c_int
_lib.hid_read_timeout.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_int]
_lib.hid_write.restype = ctypes.c_int
_lib.hid_write.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
_lib.hid_error.restype = ctypes.c_wchar_p
_lib.hid_error.argtypes = [ctypes.c_void_p]


def _hid_err(dev=None):
    msg = _lib.hid_error(dev)
    return msg if msg else "erreur hidapi inconnue"


# ═══════════════════════════════════════════════════════════════════════════════
#  Transport
# ═══════════════════════════════════════════════════════════════════════════════


class HIDTransport:
    def __init__(self, path: bytes, pid: int):
        self.path = path
        self.pid = pid
        self._dev = _lib.hid_open_path(path)
        if not self._dev:
            raise OSError(f"hid_open_path échoué : {_hid_err()}")

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    def read(self, timeout: int = 500) -> bytes | None:
        if self._dev is None:
            raise TransportError("lecture sur transport fermé")
        buf = (ctypes.c_ubyte * MAX_READ_SIZE)()
        n = _lib.hid_read_timeout(self._dev, buf, MAX_READ_SIZE, timeout)
        if n < 0:
            err = _hid_err(self._dev) or ""
            if "success" in err.lower() or err == "":
                return None  # quirk BT macOS
            raise TransportError(f"hid_read échoué : {err}")
        return bytes(buf[:n]) if n > 0 else None

    def write(self, msg: bytes) -> None:
        if self._dev is None:
            raise TransportError("écriture sur transport fermé")
        buf = (ctypes.c_ubyte * len(msg))(*msg)
        n = _lib.hid_write(self._dev, buf, len(msg))
        if n < 0:
            raise TransportError(f"hid_write échoué : {_hid_err(self._dev)}")

    def close(self):
        if self._dev is not None:
            _lib.hid_close(self._dev)
            self._dev = None


# ═══════════════════════════════════════════════════════════════════════════════
#  Protocole HID++
# ═══════════════════════════════════════════════════════════════════════════════


def _build_msg(devnumber, request_id, params):
    data = struct.pack("!H", request_id) + params
    return struct.pack("!BB18s", REPORT_LONG, devnumber, data)


def _pack_params(params):
    parts = []
    for p in params:
        if isinstance(p, int):
            parts.append(struct.pack("B", p))
        else:
            parts.append(bytes(p))
    return b"".join(parts)


def hidpp_request(transport, devnumber, request_id, *params, timeout=500):
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
        if raw[0] not in _MSG_LENGTHS or len(raw) != _MSG_LENGTHS[raw[0]]:
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


def resolve_feature(transport, devnumber, feature_code):
    """Recherche l'index de feature. Retourne l'index ou None."""
    request_id = (FEATURE_ROOT << 8) | 0x00
    reply = hidpp_request(transport, devnumber, request_id,
                          feature_code >> 8, feature_code & 0xFF, 0x00, timeout=500)
    if reply and reply[0] != 0x00:
        return reply[0]
    return None


def get_device_type(transport, devnumber, feat_idx):
    reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x20, timeout=500)
    return reply[0] if reply else None


def get_device_name(transport, devnumber, feat_idx):
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
        chars.extend(reply[:name_len - len(chars)])
    return bytes(chars).decode("utf-8", errors="replace") if chars else None


def _drain_transport(transport: HIDTransport, max_reads: int = 8) -> None:
    """Vide le buffer d'entrée HID (non-bloquant) avant d'écrire une commande.

    Quand la souris envoie beaucoup de rapports de mouvement, ces données
    saturent la file BT et retardent le traitement des commandes sortantes.
    Vider le buffer libère la voie avant l'envoi de CHANGE_HOST.
    """
    for _ in range(max_reads):
        try:
            if transport.read(timeout=0) is None:
                break
        except (TransportError, OSError):
            break


def send_change_host(transport, devnumber, feat_idx, target_host):
    """Bascule le périphérique vers target_host (base 0).

    Vide le buffer d'entrée puis envoie la commande 3× back-to-back sans délai.
    Exception sur 1er essai = erreur réelle (propagée).
    Exception sur retry = périphérique déconnecté après switch réussi (ignorée).
    """
    _drain_transport(transport)
    request_id = (feat_idx << 8) | (CHANGE_HOST_FN_SET & 0xF0) | SW_ID
    params = struct.pack("B", target_host)
    msg = _build_msg(devnumber, request_id, params)
    for attempt in range(3):
        try:
            transport.write(msg)
        except (TransportError, OSError):
            if attempt == 0:
                raise  # 1er essai échoué = transport mort avant envoi
            return   # retry échoué = switch réussi, périphérique déconnecté


def get_current_host(transport, devnumber, feat_idx):
    """Interroge CHANGE_HOST getHostInfo (fn 0). Retourne l'hôte actuel (base 0) ou None."""
    reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x00, timeout=500)
    if reply and len(reply) >= 2:
        # reply[0] = numHosts, reply[1] = currentHost
        return reply[1]
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Découverte des périphériques
# ═══════════════════════════════════════════════════════════════════════════════


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
    head = _lib.hid_enumerate(LOGITECH_VID, 0)
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
    _lib.hid_free_enumeration(head)
    candidates.sort(key=lambda x: -x[0])

    found_pids = set()
    for score, path, pid, up, usage in candidates:
        if pid in found_pids:
            continue
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
            found_pids.add(pid)
            return DeviceInfo(t, name, pid, ch)
        except (TransportError, OSError):
            t.close()
            continue
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Notifications + ping
# ═══════════════════════════════════════════════════════════════════════════════

_PING_REQUEST_ID = (FEATURE_ROOT << 8) | 0x00 | SW_ID
_PING_MSG = struct.pack("!BB18s", REPORT_LONG, DEVNUMBER_DIRECT,
                        struct.pack("!H", _PING_REQUEST_ID) + b"\x00\x00\x00")

# ── Préférences persistantes ─────────────────────────────────────────────────
_PREFS_FILE = os.path.expanduser("~/.swigi_prefs.json")


def _load_prefs() -> dict:
    try:
        with open(_PREFS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"notifications": True}


def _save_prefs(prefs: dict) -> None:
    try:
        with open(_PREFS_FILE, "w") as f:
            json.dump(prefs, f)
    except Exception:
        pass


_prefs = _load_prefs()


def _notify(message: str, subtitle: str = "") -> None:
    """Notification macOS via osascript. No-op si désactivé ou hors Darwin."""
    if _SYSTEM != "Darwin" or not _prefs.get("notifications", True):
        return
    script = f'display notification "{message}" with title "SwiGi"'
    if subtitle:
        script += f' subtitle "{subtitle}"'
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Icône menu bar macOS (rumps — installé par install_mac.sh)
# ═══════════════════════════════════════════════════════════════════════════════

if _HAS_RUMPS:
    class SwiGiMenuBar(_rumps.App):
        def __init__(self, state: dict, stop_event: threading.Event):
            initial = "⌨️" if (state.get("kb") and state.get("mouse")) else "⌨"
            super().__init__(initial, quit_button=None)
            self._state = state
            self._stop_event = stop_event
            self._kb_item = _rumps.MenuItem("Clavier : —")
            self._mouse_item = _rumps.MenuItem("Souris : —")
            self._count_item = _rumps.MenuItem("Basculements : 0")
            self._notify_item = _rumps.MenuItem("Notifications",
                                                callback=self._toggle_notify)
            self._notify_item.state = _prefs.get("notifications", True)
            self.menu = [
                self._kb_item,
                self._mouse_item,
                None,
                self._count_item,
                None,
                self._notify_item,
                _rumps.MenuItem("Masquer l'icône", callback=self._hide_icon),
                None,
                _rumps.MenuItem("Quitter", callback=self._quit),
            ]

        @_rumps.timer(2)
        def _refresh(self, _):
            kb = self._state.get("kb")
            mouse = self._state.get("mouse")
            switches = self._state.get("switches", 0)
            self._kb_item.title = f"Clavier : {kb or '—'} {'✅' if kb else '❌'}"
            self._mouse_item.title = f"Souris : {mouse or '—'} {'✅' if mouse else '❌'}"
            self._count_item.title = f"Basculements : {switches}"
            self.title = "⌨️" if (kb and mouse) else "⌨"

        def _toggle_notify(self, sender):
            enabled = not bool(sender.state)
            _prefs["notifications"] = enabled
            _save_prefs(_prefs)
            sender.state = enabled

        def _hide_icon(self, _):
            _notify("Icône masquée — relance SwiGi pour réafficher")
            try:
                self._status_item.setVisible_(False)
            except Exception:
                pass

        def _quit(self, _):
            self._stop_event.set()
            _rumps.quit_application()


# ═══════════════════════════════════════════════════════════════════════════════
#  Boucle daemon
# ═══════════════════════════════════════════════════════════════════════════════


def _run_daemon(kb: DeviceInfo, mouse: DeviceInfo,
                state: dict, stop_event: threading.Event) -> None:
    state["kb"] = kb.name
    state["mouse"] = mouse.name

    total_switches = 0
    last_response = time.time()
    last_switch_time = 0.0  # timestamp du dernier CHANGE_HOST réussi
    WATCHDOG_TIMEOUT = 10.0

    while not stop_event.is_set():
        # ── Watchdog ──
        if time.time() - last_response > WATCHDOG_TIMEOUT:
            log.info("Watchdog : aucune réponse depuis %ds, reconnexion...", int(WATCHDOG_TIMEOUT))
            kb.close()
            mouse.close()
            state["kb"] = None
            state["mouse"] = None
            time.sleep(1.0)
            kb_new = find_device(DEVICE_TYPE_KEYBOARD)
            if kb_new:
                kb = kb_new
                state["kb"] = kb.name
                log.info("Watchdog reconnexion clavier : %s", kb.name)
            mouse_new = find_device(DEVICE_TYPE_MOUSE)
            if mouse_new:
                mouse = mouse_new
                state["mouse"] = mouse.name
                log.info("Watchdog reconnexion souris : %s", mouse.name)
            last_response = time.time()
            continue

        # ── Ping ──
        try:
            kb.transport.write(_PING_MSG)
        except (TransportError, OSError):
            log.info("Clavier déconnecté, attente du retour...")
            if time.time() - last_switch_time > 3.0:
                _notify(f"{kb.name} déconnecté", "Clavier")
            kb.close()
            state["kb"] = None

            kb_new = None
            for attempt in range(600):
                if stop_event.is_set():
                    break
                time.sleep(0.1)
                kb_new = find_device(DEVICE_TYPE_KEYBOARD)
                if kb_new is not None:
                    break
                if attempt % 100 == 99:
                    log.debug("Reconnexion : tentative %d/600...", attempt + 1)

            if kb_new is None:
                if not stop_event.is_set():
                    log.warning("Le clavier n'est pas revenu, nouvelle tentative...")
                continue
            kb = kb_new
            state["kb"] = kb.name
            log.info("Reconnexion clavier : %s", kb.name)
            _notify(f"{kb.name} reconnecté", "Clavier")
            last_response = time.time()

            mouse.close()
            state["mouse"] = None
            log.debug("Reconnexion proactive de la souris...")
            new_mouse = find_device(DEVICE_TYPE_MOUSE)
            if new_mouse:
                mouse = new_mouse
                state["mouse"] = mouse.name
                log.debug("Souris prête en avance : %s", mouse.name)
                _notify(f"{mouse.name} reconnectée", "Souris")
            else:
                log.debug("Souris introuvable, nouvelle tentative au prochain événement")
            continue

        # ── Lecture réponses (fenêtre 80ms) ──
        deadline = time.time() + 0.08
        while time.time() < deadline and not stop_event.is_set():
            try:
                raw = kb.transport.read(timeout=25)
            except (TransportError, OSError):
                break

            if raw is None or len(raw) < 4:
                continue
            rid = raw[0]
            if rid not in _MSG_LENGTHS or len(raw) != _MSG_LENGTHS[rid]:
                continue

            feat = raw[2]
            func = raw[3]
            sw_id = func & 0x0F
            last_response = time.time()

            # Notification CHANGE_HOST
            if feat == kb.change_host_idx and sw_id == 0 and len(raw) > 5:
                target_host = raw[5]
                log.info("")
                log.info("★ Easy-Switch : %s → hôte %d", kb.name, target_host)

                if not mouse.transport.is_open:
                    log.debug("Transport souris fermé, reconnexion...")
                    new_mouse = find_device(DEVICE_TYPE_MOUSE)
                    if new_mouse:
                        mouse = new_mouse
                        state["mouse"] = mouse.name
                    else:
                        log.info("Souris indisponible — basculera au prochain Easy-Switch")
                        break

                try:
                    send_change_host(mouse.transport, DEVNUMBER_DIRECT,
                                     mouse.change_host_idx, target_host)
                    log.info("★ CHANGE_HOST → %s → hôte %d", mouse.name, target_host)
                    total_switches += 1
                    state["switches"] = total_switches
                    last_switch_time = time.time()
                except (TransportError, OSError):
                    log.warning("CHANGE_HOST souris échoué, reconnexion...")
                    mouse.close()
                    state["mouse"] = None
                    time.sleep(0.5)
                    new_mouse = find_device(DEVICE_TYPE_MOUSE)
                    if new_mouse:
                        mouse = new_mouse
                        state["mouse"] = mouse.name
                        try:
                            send_change_host(mouse.transport, DEVNUMBER_DIRECT,
                                             mouse.change_host_idx, target_host)
                            log.info("★ CHANGE_HOST → %s → hôte %d (après reconnexion)",
                                     mouse.name, target_host)
                            total_switches += 1
                            state["switches"] = total_switches
                        except (TransportError, OSError) as e:
                            log.warning("Retry CHANGE_HOST échoué : %s", e)
                    else:
                        log.info("Souris indisponible — basculera au prochain Easy-Switch")

                break  # le clavier va se déconnecter

            if sw_id == 0:
                log.debug("Notification : feat=0x%02X [%s]", feat, raw[:10].hex())

        time.sleep(0.01)

    log.info("Arrêt. Total : %d basculements.", total_switches)
    kb.close()
    mouse.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Point d'entrée
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="SwiGi — synchronisation Easy-Switch via Bluetooth")
    parser.add_argument("-v", "--verbose", action="store_true", help="Journalisation détaillée")
    parser.add_argument(
        "--log-file", metavar="FICHIER",
        help="Écrire les logs dans ce fichier (rotation auto : 1 Mo × 3)",
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root_logger.addHandler(ch)

    if args.log_file:
        fh = logging.handlers.RotatingFileHandler(
            args.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)

    log.info("SwiGi — recherche des périphériques...")

    kb = find_device(DEVICE_TYPE_KEYBOARD)
    if kb is None:
        log.error("Clavier introuvable ! Vérifie la connexion Bluetooth.")
        return 1
    log.info("Clavier : %s (CHANGE_HOST idx=%d)", kb.name, kb.change_host_idx)
    _notify(f"{kb.name} connecté", "Clavier")

    mouse = find_device(DEVICE_TYPE_MOUSE)
    if mouse is None:
        log.error("Souris introuvable ! Vérifie la connexion Bluetooth.")
        kb.close()
        return 1
    log.info("Souris :  %s (CHANGE_HOST idx=%d)", mouse.name, mouse.change_host_idx)
    _notify(f"{mouse.name} connectée", "Souris")

    log.info("")
    log.info("Prêt. Appuie sur Easy-Switch sur %s.", kb.name)
    if not _HAS_RUMPS:
        log.info("Ctrl+C pour quitter.")

    state: dict = {"kb": kb.name, "mouse": mouse.name, "switches": 0}
    stop_event = threading.Event()

    def _on_stop(sig, frame):
        stop_event.set()
        if _HAS_RUMPS:
            try:
                _rumps.quit_application()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    if _HAS_RUMPS:
        # Daemon en thread background, menu bar sur thread principal (requis AppKit)
        t = threading.Thread(
            target=_run_daemon, args=(kb, mouse, state, stop_event), daemon=True
        )
        t.start()
        SwiGiMenuBar(state, stop_event).run()
        stop_event.set()
        t.join(timeout=3)
    else:
        _run_daemon(kb, mouse, state, stop_event)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
