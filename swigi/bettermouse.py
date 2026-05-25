"""Intégration BetterMouse — lecture, export et application de profils souris.

macOS uniquement. No-op silencieux sur Windows/Linux.
Toutes les opérations de lecture/écriture plist sont protégées par try/except
avec rollback automatique avant toute modification du fichier BetterMouse.
"""
from __future__ import annotations

import json
import logging
import os
import plistlib
import shutil
import subprocess
from datetime import datetime

from swigi.constants import SYSTEM

log = logging.getLogger("swigi.bettermouse")

BM_PLIST = os.path.expanduser(
    "~/Library/Preferences/com.naotanhaocan.BetterMouse.plist"
)
PROFILES_DIR = os.path.expanduser("~/.swigi_profiles")

# Clés de prefs BetterMouse à ne jamais exporter (données sensibles / licence)
_SENSITIVE_KEYS = {"Paddle-BetterMouse-760932-SD", "Paddle-BetterMouse-760932-SD"}


def is_available() -> bool:
    """Retourne True si BetterMouse est installé et a déjà été lancé."""
    return SYSTEM == "Darwin" and os.path.isfile(BM_PLIST)


def list_profiles() -> list[str]:
    """Retourne les noms de profils disponibles (sans extension .json)."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    try:
        return sorted(
            f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".json")
        )
    except OSError:
        return []


def _decode_root() -> dict:
    with open(BM_PLIST, "rb") as f:
        return plistlib.load(f)


def _find_global_app(apps: dict) -> dict | None:
    """Retourne le profil global (url.relative == './')."""
    for v in apps.values():
        if isinstance(v, dict):
            url = v.get("url", {})
            if isinstance(url, dict) and url.get("relative") == "./":
                return v
    return None


def _polling_hz(rp_rate_list: int, rp_rate: int) -> int:
    """Déduit la fréquence de polling en Hz depuis les bitmask BetterMouse."""
    rates = []
    mapping = {0: 125, 1: 250, 2: 500, 3: 1000, 4: 2000, 5: 4000, 6: 8000}
    for bit, hz in mapping.items():
        if rp_rate_list & (1 << bit):
            rates.append(hz)
    return rates[rp_rate] if rates and rp_rate < len(rates) else 0


def read_info() -> dict | None:
    """Lit les infos de base (nom souris, polling, molette) sans modifier quoi que ce soit."""
    if not is_available():
        return None
    try:
        root = _decode_root()
        mice = plistlib.loads(root["mice"]).get("mice", [])
        mouse = next(
            (m for m in mice if m.get("name", {}).get("vendor", "").lower() == "logitech"),
            None,
        )
        if not mouse:
            return None
        return {
            "name":         mouse.get("name", {}).get("product", "?"),
            "vendor":       mouse.get("name", {}).get("vendor", "?"),
            "hireswheel":   mouse.get("hiResWheel", False),
            "ratchet":      mouse.get("ratchetMode", False),
            "dpi_en":       mouse.get("dpiEn", False),
            "polling_hz":   _polling_hz(mouse.get("rpRateList", 0), mouse.get("rpRate", 0)),
        }
    except Exception as e:
        log.debug("read_info échoué : %s", e)
        return None


def export_current(name: str | None = None) -> str:
    """Lit BetterMouse, sauve un snapshot JSON dans PROFILES_DIR.

    Retourne le chemin du fichier créé.
    Lève FileNotFoundError si BetterMouse est absent.
    """
    if not is_available():
        raise FileNotFoundError("BetterMouse plist introuvable")

    root = _decode_root()

    mice = plistlib.loads(root["mice"]).get("mice", [])
    appitems = plistlib.loads(root["appitems"]).get("apps", {})

    mouse = next(
        (m for m in mice if m.get("name", {}).get("vendor", "").lower() == "logitech"),
        {},
    )
    global_app = _find_global_app(appitems) or {}
    scl = global_app.get("scl", {})

    profile_name = name or datetime.now().strftime("profil-%Y%m%d-%H%M")
    profile = {
        "meta": {
            "name":        profile_name,
            "bm_version":  str(root.get("version", "?")),
            "mouse":       mouse.get("name", {}).get("product", "?"),
            "exported_at": datetime.now().isoformat(),
        },
        "scroll": {
            "smooth_en":   bool(scl.get("smoothEn", True)),
            "scl_through": bool(scl.get("sclThrough", True)),
            "duration":    int(scl.get("duration", 10)),
            "brake":       int(scl.get("brake", 10)),
            "panel_lpf":   int(scl.get("panelLpf", 2)),
            "lpf_dura":    int(scl.get("lpfDura", 14)),
            "vert_inv":    bool(scl.get("vertInvEn", False)),
            "hori_inv":    bool(scl.get("horiInvEn", False)),
            "hori_speed":  float(scl.get("horiSpeed", 8.0)),
        },
        "mouse_hw": {
            "ratchet":         bool(mouse.get("ratchetMode", True)),
            "hireswheel":      bool(mouse.get("hiResWheel", True)),
            "disengage_point": int(mouse.get("disengagePoint", 14)),
            "torque":          int(mouse.get("torque", 70)),
            "dpi_en":          bool(mouse.get("dpiEn", False)),
            "dpi_index":       int(mouse.get("dpiIndex", 0)),
            "polling_rate":    int(mouse.get("rpRate", 0)),
        },
    }

    os.makedirs(PROFILES_DIR, exist_ok=True)
    path = os.path.join(PROFILES_DIR, f"{profile_name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    log.info("Profil exporté : %s", path)
    return path


def apply_profile(name: str, mouse_name: str | None = None) -> None:
    """Applique un profil JSON à BetterMouse.

    Patch le plist BetterMouse (backup auto → patch → restart BM).
    Si patch échoue → rollback automatique vers backup.
    Lève FileNotFoundError si profil ou plist absent.
    Lève ValueError si le profil ne correspond pas à la souris connectée.
    """
    if not is_available():
        raise FileNotFoundError("BetterMouse plist introuvable")

    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Profil introuvable : {path}")

    with open(path, encoding="utf-8") as f:
        profile = json.load(f)

    # Vérification souris (optionnelle si mouse_name fourni)
    if mouse_name and profile.get("meta", {}).get("mouse") not in (None, "?", mouse_name):
        raise ValueError(
            f"Profil pour '{profile['meta']['mouse']}', souris connectée : '{mouse_name}'"
        )

    backup = BM_PLIST + ".swigi_bak"
    shutil.copy2(BM_PLIST, backup)
    log.debug("Backup BetterMouse plist → %s", backup)

    try:
        root = _decode_root()
        _patch_appitems(root, profile.get("scroll", {}))
        _patch_mice(root, profile.get("mouse_hw", {}))

        with open(BM_PLIST, "wb") as f:
            plistlib.dump(root, f, fmt=plistlib.FMT_BINARY)
        log.info("Profil '%s' appliqué", name)

    except Exception as e:
        log.error("Patch BetterMouse échoué, rollback : %s", e)
        shutil.copy2(backup, BM_PLIST)
        raise

    finally:
        subprocess.run(["killall", "BetterMouse"], check=False, capture_output=True)
        subprocess.Popen(
            ["open", "-a", "BetterMouse"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _patch_appitems(root: dict, scroll: dict) -> None:
    if not scroll:
        return
    appitems_raw = plistlib.loads(root["appitems"])
    apps = appitems_raw.get("apps", {})
    global_app = _find_global_app(apps)
    if global_app is None:
        return
    scl = global_app.setdefault("scl", {})
    _safe_update(scl, {
        "smoothEn":   scroll.get("smooth_en"),
        "sclThrough": scroll.get("scl_through"),
        "duration":   scroll.get("duration"),
        "brake":      scroll.get("brake"),
        "panelLpf":   scroll.get("panel_lpf"),
        "lpfDura":    scroll.get("lpf_dura"),
        "vertInvEn":  scroll.get("vert_inv"),
        "horiInvEn":  scroll.get("hori_inv"),
        "horiSpeed":  scroll.get("hori_speed"),
    })
    root["appitems"] = plistlib.dumps(appitems_raw, fmt=plistlib.FMT_BINARY)


def _patch_mice(root: dict, hw: dict) -> None:
    if not hw:
        return
    mice_raw = plistlib.loads(root["mice"])
    mice = mice_raw.get("mice", [])
    for m in mice:
        if m.get("name", {}).get("vendor", "").lower() == "logitech":
            _safe_update(m, {
                "ratchetMode":    hw.get("ratchet"),
                "hiResWheel":     hw.get("hireswheel"),
                "disengagePoint": hw.get("disengage_point"),
                "torque":         hw.get("torque"),
                "dpiEn":          hw.get("dpi_en"),
                "dpiIndex":       hw.get("dpi_index"),
                "rpRate":         hw.get("polling_rate"),
            })
            break
    root["mice"] = plistlib.dumps(mice_raw, fmt=plistlib.FMT_BINARY)


def _safe_update(target: dict, updates: dict) -> None:
    """Met à jour target avec les valeurs non-None de updates."""
    for k, v in updates.items():
        if v is not None:
            target[k] = v
