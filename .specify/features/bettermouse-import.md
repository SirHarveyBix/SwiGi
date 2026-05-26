# Spec : Profils BetterMouse par hôte

**Version :** 0.2.0
**Date :** 2026-05-23
**Statut :** Étude validée — à implémenter

---

## 1. Vision

Chaque Mac a SwiGi + BetterMouse qui tournent indépendamment. Quand la souris arrive sur un Mac (après Easy-Switch), ce Mac peut appliquer automatiquement un profil BetterMouse spécifique.

**Exemple concret :**

| Machine    | Hôte | Profil actif                                          |
| ---------- | ---- | ----------------------------------------------------- |
| Mac bureau | 0    | config-bureau.json (sensibilité haute, ratchet ON)    |
| MacBook    | 1    | config-laptop.json (sensibilité basse, hi-res scroll) |
| Mac mini   | 2    | config-bureau.json (même profil que le bureau)        |

Mac mini **choisit** d'utiliser le profil exporté depuis Mac bureau — pas le sien propre.

---

## 2. Architecture

```
[Mac 1 — bureau]                    [Mac 2 — laptop]
  SwiGi menu bar                      SwiGi menu bar
    → "Exporter config actuelle"         → "Profil au connect: config-bureau"
    → sauve config-bureau.json           → l'utilisateur a copié config-bureau.json
                                         → au connect souris : applique le profil
```

Chaque Mac stocke localement :

```
~/.swigi_profiles/
    config-bureau.json   ← snapshot BetterMouse exporté
    config-laptop.json
~/.swigi_prefs.json      ← "bm_profile": "config-bureau", "bm_auto_apply": true
```

Le partage entre Macs est **manuel** (AirDrop, iCloud Drive, etc.) ou via un dossier partagé configurable. SwiGi ne crée pas de réseau entre les machines.

---

## 3. Ce que contient un profil exporté

Lecture depuis `~/Library/Preferences/com.naotanhaocan.BetterMouse.plist` :

```json
{
  "meta": {
    "name": "config-bureau",
    "bm_version": "8830",
    "mouse": "MX Master 4",
    "exported_at": "2026-05-23T10:00:00"
  },
  "scroll": {
    "smooth_en": true,
    "speed": [...],
    "acceleration": [...],
    "duration": 10,
    "brake": 10,
    "vert_inv": true,
    "hori_inv": false,
    "hori_speed": 8.0,
    "hireswheel": true,
    "ratchet": true,
    "disengage_point": 14,
    "torque": 70
  },
  "cursor": {
    "en": false,
    "acceleration": [...],
    "resolution": [...]
  },
  "buttons": { ... },
  "polling_rate_index": 0
}
```

Format JSON lisible, copiable, partageable. Pas de données sensibles (clé de licence `Paddle-*` exclue).

---

## 4. Menu bar — actions proposées

```
Clavier : MX Keys ✅
Souris  : MX Master 4 ✅
Basculements : 12
────────────────────────
Profil souris
  ├─ Exporter config actuelle…
  ├─ Profil au connect : [aucun] ▸ config-bureau ▸ config-laptop
  └─ Appliquer auto au connect : ✅
────────────────────────
Notifications : ✅
Masquer l'icône
Quitter
```

**"Exporter config actuelle…"** → lit BetterMouse plist → enregistre `~/.swigi_profiles/<nom>.json` → dialogue nom (via rumps.Window ou nom auto horodaté).

**"Profil au connect"** → sous-menu avec les `.json` trouvés dans `~/.swigi_profiles/` → sélection stockée dans `~/.swigi_prefs.json`.

**"Appliquer auto au connect"** → toggle. Si ON : quand la souris arrive sur ce Mac (sonde périodique ou proactif), le profil sélectionné est appliqué.

---

## 5. Application du profil — contrainte technique

BetterMouse n'a pas d'API publique (pas d'AppleScript dictionary, pas d'URL scheme documenté). Deux approches possibles :

### Option A — Écriture plist + redémarrage BetterMouse (brute force)

```python
import subprocess, shutil, plistlib

# 1. Backup
shutil.copy(BM_PLIST, BM_PLIST + ".bak")
# 2. Lire plist actuel
with open(BM_PLIST, "rb") as f:
    root = plistlib.load(f)
# 3. Patcher les clés (scroll, cursor…) depuis le profil JSON
root["mice"] = _patch_mice(root["mice"], profile)
# 4. Écrire
with open(BM_PLIST, "wb") as f:
    plistlib.dump(root, f, fmt=plistlib.FMT_BINARY)
# 5. Relancer BetterMouse
subprocess.run(["killall", "BetterMouse"], check=False)
subprocess.Popen(["open", "-a", "BetterMouse"])
```

**Avantages :** Fonctionne avec toute version BetterMouse. Aucune permission spéciale.

**Risques :**

- Changements non sauvegardés dans BetterMouse perdus (mitigé par backup auto)
- Si la structure plist change entre versions → patcher échoue silencieusement (try/except + fallback backup restore)
- Délai ~1s le temps que BetterMouse redémarre

### Option B — Lecture seule + notification utilisateur

Si l'écriture plist est jugée trop risquée : SwiGi lit le profil et notifie "Profil config-bureau chargé — applique dans BetterMouse si besoin". Moins puissant mais zéro risque.

**Recommandation : implémenter Option A avec backup automatique et rollback sur erreur.**

---

## 6. Flux complet au switch

```
Easy-Switch pressé (hôte 0 → hôte 1)
  → SwiGi envoie CHANGE_HOST souris
  → souris arrive sur hôte 1 (MacBook)
  → SwiGi MacBook : sonde détecte souris
  → state["pending_host"] vérifié (Phase 2)
  → si bm_auto_apply == true ET bm_profile configuré :
      → lire ~/.swigi_profiles/config-laptop.json
      → patch plist BetterMouse
      → killall BetterMouse && open -a BetterMouse
      → notify "Profil config-laptop appliqué"
```

---

## 7. Nouveau module `swigi/bettermouse.py`

```python
import json
import logging
import os
import plistlib
import shutil
import subprocess
from datetime import datetime

log = logging.getLogger("swigi.bettermouse")

BM_PLIST = os.path.expanduser(
    "~/Library/Preferences/com.naotanhaocan.BetterMouse.plist"
)
PROFILES_DIR = os.path.expanduser("~/.swigi_profiles")


def is_available() -> bool:
    return os.path.isfile(BM_PLIST)


def list_profiles() -> list[str]:
    """Retourne les noms de profils disponibles (sans extension)."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    return [f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".json")]


def export_current(name: str | None = None) -> str:
    """Lit BetterMouse, sauve un snapshot JSON. Retourne le chemin."""
    if not is_available():
        raise FileNotFoundError("BetterMouse plist introuvable")

    with open(BM_PLIST, "rb") as f:
        root = plistlib.load(f)

    mice = plistlib.loads(root["mice"]).get("mice", [])
    cfg = plistlib.loads(root["config"])
    appitems = plistlib.loads(root["appitems"]).get("apps", {})

    mouse_data = next(
        (m for m in mice if m.get("name", {}).get("vendor", "").lower() == "logitech"),
        {},
    )
    global_scl = list(appitems.values())[0].get("scl", {}) if appitems else {}

    profile = {
        "meta": {
            "name": name or datetime.now().strftime("profil-%Y%m%d-%H%M"),
            "bm_version": root.get("version", "?"),
            "mouse": mouse_data.get("name", {}).get("product", "?"),
            "exported_at": datetime.now().isoformat(),
        },
        "scroll": {
            "smooth_en":      global_scl.get("smoothEn", True),
            "duration":       global_scl.get("duration", 10),
            "brake":          global_scl.get("brake", 10),
            "vert_inv":       global_scl.get("vertInvEn", False),
            "hori_inv":       global_scl.get("horiInvEn", False),
            "hori_speed":     global_scl.get("horiSpeed", 8.0),
        },
        "mouse_hw": {
            "ratchet":        mouse_data.get("ratchetMode", True),
            "hireswheel":     mouse_data.get("hiResWheel", True),
            "disengage_point":mouse_data.get("disengagePoint", 14),
            "torque":         mouse_data.get("torque", 70),
            "dpi_en":         mouse_data.get("dpiEn", False),
            "dpi_index":      mouse_data.get("dpiIndex", 0),
            "polling_rate":   mouse_data.get("rpRate", 0),
        },
    }

    os.makedirs(PROFILES_DIR, exist_ok=True)
    path = os.path.join(PROFILES_DIR, f"{profile['meta']['name']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    log.info("Profil exporté : %s", path)
    return path


def apply_profile(name: str) -> None:
    """Applique un profil JSON à BetterMouse (plist patch + redémarrage)."""
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Profil introuvable : {path}")
    if not is_available():
        raise FileNotFoundError("BetterMouse plist introuvable")

    with open(path, encoding="utf-8") as f:
        profile = json.load(f)

    # Backup avant modification
    backup = BM_PLIST + ".swigi_bak"
    shutil.copy2(BM_PLIST, backup)

    try:
        with open(BM_PLIST, "rb") as f:
            root = plistlib.load(f)

        # Patch appitems (scroll global)
        appitems_raw = plistlib.loads(root["appitems"])
        apps = appitems_raw.get("apps", {})
        global_key = next((k for k in apps if apps[k].get("url", {}).get("relative") == "./"), None)
        if global_key and "scroll" in profile:
            scl = apps[global_key].setdefault("scl", {})
            s = profile["scroll"]
            scl.update({
                "smoothEn":  s.get("smooth_en", scl.get("smoothEn", True)),
                "duration":  s.get("duration",  scl.get("duration",  10)),
                "brake":     s.get("brake",      scl.get("brake",     10)),
                "vertInvEn": s.get("vert_inv",   scl.get("vertInvEn", False)),
                "horiInvEn": s.get("hori_inv",   scl.get("horiInvEn", False)),
                "horiSpeed": s.get("hori_speed", scl.get("horiSpeed", 8.0)),
            })
        root["appitems"] = plistlib.dumps(appitems_raw, fmt=plistlib.FMT_BINARY)

        # Patch mice (hardware souris)
        mice_raw = plistlib.loads(root["mice"])
        mice = mice_raw.get("mice", [])
        hw = profile.get("mouse_hw", {})
        for m in mice:
            if m.get("name", {}).get("vendor", "").lower() == "logitech":
                m.update({k: v for k, v in {
                    "ratchetMode":    hw.get("ratchet"),
                    "hiResWheel":     hw.get("hireswheel"),
                    "disengagePoint": hw.get("disengage_point"),
                    "torque":         hw.get("torque"),
                    "dpiEn":          hw.get("dpi_en"),
                    "dpiIndex":       hw.get("dpi_index"),
                }.items() if v is not None})
                break
        root["mice"] = plistlib.dumps(mice_raw, fmt=plistlib.FMT_BINARY)

        with open(BM_PLIST, "wb") as f:
            plistlib.dump(root, f, fmt=plistlib.FMT_BINARY)

        log.info("Profil '%s' appliqué à BetterMouse", name)

    except Exception as e:
        log.error("Échec patch BetterMouse, restauration backup : %s", e)
        shutil.copy2(backup, BM_PLIST)
        raise

    finally:
        # Redémarrer BetterMouse pour prendre en compte les changements
        subprocess.run(["killall", "BetterMouse"], check=False)
        subprocess.Popen(["open", "-a", "BetterMouse"])
```

---

## 8. Intégration `swigi/gui.py`

```python
# Dans SwiGiMenuBar.menu :
_rumps.MenuItem("Exporter config BetterMouse…", callback=self._bm_export),
_rumps.MenuItem("Profil au connect", callback=None),   # sous-menu dynamique
_rumps.MenuItem("Auto-appliquer au connect", callback=self._bm_toggle_auto),
```

```python
def _bm_export(self, _):
    from swigi.bettermouse import export_current, is_available
    if not is_available():
        notify("BetterMouse introuvable", "SwiGi")
        return
    try:
        path = export_current()
        name = os.path.basename(path)
        notify(f"Exporté : {name}", "BetterMouse")
        self._rebuild_profile_menu()
    except Exception as e:
        notify(f"Export échoué : {e}", "Erreur")

def _bm_toggle_auto(self, sender):
    prefs["bm_auto_apply"] = not prefs.get("bm_auto_apply", False)
    save_prefs(prefs)
    sender.state = prefs["bm_auto_apply"]

def _rebuild_profile_menu(self):
    from swigi.bettermouse import list_profiles
    # Reconstruire le sous-menu "Profil au connect"
    ...
```

```python
# Dans run_daemon / sonde périodique, après reconnexion souris :
if prefs.get("bm_auto_apply") and prefs.get("bm_profile"):
    from swigi.bettermouse import apply_profile
    try:
        apply_profile(prefs["bm_profile"])
        notify(f"Profil {prefs['bm_profile']} appliqué", "BetterMouse")
    except Exception as e:
        log.warning("Profil BetterMouse non appliqué : %s", e)
```

---

## 9. Risques et mitigations

| Risque                                         | Probabilité             | Mitigation                                                       |
| ---------------------------------------------- | ----------------------- | ---------------------------------------------------------------- |
| Structure plist change entre versions BM       | Moyen                   | try/except + rollback backup automatique                         |
| BetterMouse relancé pendant une session active | Faible                  | Délai ~1s acceptable, même comportement qu'un redémarrage manuel |
| Plusieurs souris Logitech → mauvaise cible     | Possible                | Matcher `mice[i].name.product == swigi_mouse.name`               |
| Profil d'une autre souris appliqué             | Possible                | Vérifier `profile.meta.mouse == swigi_mouse.name` avant apply    |
| BetterMouse absent sur ce Mac                  | Certain (Windows/Linux) | Guard `if SYSTEM == "Darwin" and is_available()`                 |
| Clé de licence dans plist                      | Oui (Paddle-\*)         | Jamais incluse dans l'export JSON                                |

---

## 10. Conformité constitution

| Principe        | Impact        | Mesure                                                                              |
| --------------- | ------------- | ----------------------------------------------------------------------------------- |
| Simplicité      | ✅            | Module opt-in, chargé uniquement si BetterMouse présent                             |
| Portabilité     | ⚠️ macOS-only | Guard `SYSTEM == "Darwin"` — no-op Windows/Linux                                    |
| Robustesse      | ✅            | Backup avant écriture plist, rollback sur erreur                                    |
| Non-intrusivité | ⚠️            | Écriture plist + redémarrage BetterMouse — opt-in explicite (toggle OFF par défaut) |
| Réactivité      | ✅            | Apply ~1s (BetterMouse restart), acceptable post-switch                             |

---

## 11. Phases d'implémentation

**Phase 1 — Export + menu bar** (pas d'application automatique)

- `bettermouse.py` : `is_available()`, `export_current()`, `list_profiles()`
- `gui.py` : bouton export, sous-menu sélection profil (stocké dans prefs)
- Toggle auto-apply = OFF par défaut

**Phase 2 — Application automatique**

- `bettermouse.py` : `apply_profile()`
- `daemon.py` / `gui.py` : déclenchement post-reconnect souris
- Test sur machine réelle requis avant activation par défaut
