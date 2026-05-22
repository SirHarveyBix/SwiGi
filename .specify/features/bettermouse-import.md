# Spec : Import profil BetterMouse

**Version :** 0.1.0 (brouillon)
**Date :** 2026-05-23
**Statut :** Étude — non implémenté

---

## 1. Contexte

BetterMouse est un outil macOS de personnalisation avancée de souris (accélération, DPI, scroll, remapping boutons). Il stocke sa configuration dans un plist binaire accessible sans permission particulière. SwiGi et BetterMouse opèrent sur la même souris physique mais à des couches différentes :

| Outil       | Couche              | Protocole     |
|-------------|---------------------|---------------|
| SwiGi       | Easy-Switch (hôte)  | HID++ BT      |
| BetterMouse | Comportement souris | IOKit / HIDManager |

L'idée : offrir dans le menu bar SwiGi une action **"Importer depuis BetterMouse"** qui lit la config de la souris détectée et permet à terme d'appliquer un profil différent selon l'hôte cible.

---

## 2. Emplacement et format de la config BetterMouse

### Fichier principal
```
~/Library/Preferences/com.naotanhaocan.BetterMouse.plist
```
Format : plist binaire (Apple binary property list), lisible via `plistlib` (stdlib Python).

### Structure des clés pertinentes

```
config        → blob plist : config globale (accélération curseur, longPressPeriod…)
mice          → blob plist : liste des souris connues
  └─ mice[0]
       ├─ name.product      = "MX Master 4"
       ├─ name.vendor       = "Logitech"
       ├─ dpiEn             = bool
       ├─ dpiIndex          = int
       ├─ ratchetMode       = bool
       ├─ disengagePoint    = int  (point de décrochage molette)
       ├─ torque            = int
       ├─ hiResWheel        = bool
       ├─ pressure          = int
       ├─ rpRate            = int  (polling rate index)
       ├─ rpRateList        = int  (polling rates disponibles, bitmask)
       └─ featureMap        = dict (feature HID++ index → BetterMouse feature id)

appitems      → blob plist : profil par application
  └─ apps[0]   (entrée globale, url = './')
       ├─ scl   → paramètres scroll (speed, acc, inversion, durée, brake…)
       └─ btn   → remapping boutons (par bouton, par geste, par app)

logikeys      → blob plist : claviers Logitech connus
  └─ logiKeybs[0]
       ├─ name.product    = "MX Keys" / "MX Keys Mini"
       ├─ platformList    → mappage hôte BetterMouse (index=0,1,2 ↔ desc=0,1,2)
       ├─ backLight       = int
       ├─ fnInversion     = bool
       └─ featureMap      = dict
```

### Clé `logikeys.logiKeybs[i].platformList`
C'est la plus utile pour SwiGi. Chaque entrée :
```
{ index: 0, desc: 0, osMask: 1 }   → hôte 0 = macOS
{ index: 1, desc: 1, osMask: 32 }  → hôte 1 = Windows
{ index: 2, desc: 2, osMask: 64 }  → hôte 2 = Linux
```
`osMask` : 1=macOS, 32=Windows, 64=Linux, 4=iPadOS, 128=Android.

---

## 3. Ce qu'on peut lire sans permission supplémentaire

| Info                      | Clé plist                          | Utilité SwiGi                         |
|---------------------------|------------------------------------|---------------------------------------|
| Nom de la souris          | `mice[0].name.product`             | Confirmer que c'est la même souris    |
| Polling rate actuel       | `mice[0].rpRate`                   | Affichage info dans menu bar          |
| Ratchet / Hi-res wheel    | `mice[0].ratchetMode`, `hiResWheel`| Affichage info                        |
| DPI activé + index        | `mice[0].dpiEn`, `dpiIndex`        | Affichage info                        |
| Profil scroll global      | `appitems.apps[0].scl`             | Export vers config SwiGi              |
| Profil boutons global     | `appitems.apps[0].btn`             | Affichage info (remapping)            |
| Profil hôte clavier       | `logikeys[0].platformList`         | Déduire OS par hôte                   |

---

## 4. Ce qu'on NE peut PAS faire

- **Modifier** la config BetterMouse sans passer par ses APIs privées (pas documentées).
- **Appliquer** un profil BetterMouse par hôte au moment du switch : BetterMouse ne s'active pas via plist seul (daemon en cours + IOKit). Il faudrait le notifier via AppleScript ou XPC — non documenté.
- **Créer** des profils par hôte dans BetterMouse via SwiGi : BetterMouse ne supporte pas ce concept nativement.

---

## 5. Cas d'usage réaliste

### 5.1 Auto-détection souris depuis BetterMouse (utilité immédiate)
SwiGi peut confirmer que la souris connectée en HID++ correspond à celle configurée dans BetterMouse :
```python
bm_mouse_name = read_bettermouse_mouse_name()  # "MX Master 4"
swigi_mouse_name = mouse.name                  # "MX Master 4" (depuis HID++)
if bm_mouse_name == swigi_mouse_name:
    log.info("Souris SwiGi ↔ BetterMouse confirmée : %s", bm_mouse_name)
```

### 5.2 Affichage infos souris dans le menu bar
Enrichir le menu SwiGi avec des infos lues depuis BetterMouse :
```
Souris : MX Master 4 ✅
  ├─ Polling : 1000 Hz
  ├─ Molette : Hi-Res ✅  Ratchet ✅
  └─ DPI     : désactivé
```

### 5.3 Déduction de l'OS par hôte (feature future)
Depuis `logikeys[i].platformList`, SwiGi peut savoir que :
- Hôte 0 = macOS (osMask=1)
- Hôte 1 = Windows (osMask=32)
- Hôte 2 = Linux (osMask=64)

Et afficher dans le menu : `★ Easy-Switch → Windows` au lieu de `→ hôte 1`.

---

## 6. Implémentation proposée

### 6.1 Nouveau module `swigi/bettermouse.py`

```python
import os
import plistlib
import logging

log = logging.getLogger("swigi.bettermouse")
_PLIST = os.path.expanduser("~/Library/Preferences/com.naotanhaocan.BetterMouse.plist")

def is_available() -> bool:
    return os.path.isfile(_PLIST)

def read_profile() -> dict | None:
    """Lit et décode le profil BetterMouse. Retourne None si indisponible."""
    if not is_available():
        return None
    try:
        with open(_PLIST, "rb") as f:
            root = plistlib.load(f)

        mice = plistlib.loads(root["mice"]).get("mice", [])
        logikeys = plistlib.loads(root["logikeys"]).get("logiKeybs", [])
        appitems = plistlib.loads(root["appitems"]).get("apps", {})

        # Profil souris (1ère souris Logitech trouvée)
        mouse_profile = None
        for m in mice:
            if m.get("name", {}).get("vendor", "").lower() == "logitech":
                mouse_profile = {
                    "name":       m["name"].get("product", "?"),
                    "vendor":     m["name"].get("vendor", "?"),
                    "dpi_en":     m.get("dpiEn", False),
                    "dpi_index":  m.get("dpiIndex", 0),
                    "ratchet":    m.get("ratchetMode", False),
                    "hireswheel": m.get("hiResWheel", False),
                    "polling_rate_list": m.get("rpRateList", 0),
                }
                break

        # Mapping hôte → OS (depuis 1er clavier Logitech)
        host_os_map = {}
        _os_names = {1: "macOS", 4: "iPadOS", 32: "Windows", 64: "Linux", 128: "Android"}
        for kb in logikeys:
            pl = kb.get("platformList", [])
            if pl:
                for entry in pl:
                    host_os_map[entry["index"]] = _os_names.get(entry.get("osMask", 0), "?")
                break

        return {
            "mouse": mouse_profile,
            "host_os_map": host_os_map,
        }
    except Exception as e:
        log.debug("Lecture BetterMouse échouée : %s", e)
        return None
```

### 6.2 Intégration menu bar (`swigi/gui.py`)

Nouveau bouton dans `SwiGiMenuBar.menu` :
```python
_rumps.MenuItem("Importer depuis BetterMouse", callback=self._import_bettermouse),
```

Callback :
```python
def _import_bettermouse(self, _):
    from swigi.bettermouse import is_available, read_profile
    if not is_available():
        notify("BetterMouse non installé ou jamais lancé", "SwiGi")
        return
    profile = read_profile()
    if not profile or not profile.get("mouse"):
        notify("Aucune souris Logitech trouvée dans BetterMouse", "SwiGi")
        return
    m = profile["mouse"]
    host_map = profile.get("host_os_map", {})
    # Mettre à jour les items de menu
    self._bm_item.title = f"BetterMouse : {m['name']}"
    # Afficher les hôtes OS si disponibles
    if host_map:
        hosts_str = "  ".join(f"H{k}={v}" for k,v in sorted(host_map.items()))
        self._bm_hosts_item.title = f"Hôtes : {hosts_str}"
    notify(f"Profil importé : {m['name']}", "BetterMouse")
```

---

## 7. Risques et limites

| Risque | Probabilité | Mitigation |
|--------|-------------|------------|
| Structure plist change entre versions BetterMouse | Moyen | Try/except sur chaque accès, fallback gracieux |
| Plusieurs souris Logitech configurées | Possible | Matcher sur `mice[i].name.product == swigi_mouse.name` |
| BetterMouse jamais lancé → plist absent | Probable | `is_available()` check avant tout accès |
| Données sensibles dans le plist | Non (QR codes licence) | Ne pas loguer le champ `Paddle-BetterMouse-*` |

---

## 8. Conformité constitution

| Principe        | Impact         | Mesure                                              |
|-----------------|----------------|-----------------------------------------------------|
| Simplicité      | ✅ Opt-in     | Module séparé, importé uniquement si BetterMouse présent |
| Portabilité     | ⚠️ macOS-only | Guard `if SYSTEM == "Darwin"` — no-op sur autres OS |
| Robustesse      | ✅             | Tous les accès plist dans try/except                |
| Non-intrusivité | ✅             | Lecture seule, aucune modification de la config BM  |
| Réactivité      | ✅ Neutre      | Import à la demande (clic menu), pas en background  |

---

## 9. Priorité

**Phase 1 (utile maintenant)** — `read_profile()` + affichage dans menu bar :
- Nom souris BetterMouse vs SwiGi (confirmation)
- Mapping hôte → OS (labels `H0=macOS H1=Windows`)

**Phase 2 (feature future)** — Profil par hôte :
- Stocker dans `~/.swigi_prefs.json` les préférences par hôte
- Au switch, appliquer un réglage différent (nécessite API BetterMouse non documentée ou AppleScript)
