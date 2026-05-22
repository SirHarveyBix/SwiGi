# Spec : Architecture modulaire — package Python

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

SwiGi était initialement un fichier unique (`swigi.py`). Avec l'ajout de fonctionnalités (menu bar macOS, notifications, vérification de synchronisation, log rotation), le fichier a dépassé les 800 lignes et mélange transport HID, protocole HID++, logique daemon, GUI, et CLI.

Le passage à un package Python (`swigi/`) répond à trois objectifs :

- **Testabilité** : chaque module peut être importé et testé indépendamment, y compris sans matériel HID connecté.
- **Maintenabilité** : séparation des responsabilités — un bug dans la GUI ne nécessite pas de naviguer dans le code protocole.
- **Lisibilité** : chaque fichier a un rôle clair et documenté, facilitant les contributions.

## 2. Périmètre

**Inclus :**

- Réorganisation du code en package `swigi/` avec modules spécialisés
- Wrapper de compatibilité `swigi.py` à la racine
- Support de `python3 -m swigi` via `__main__.py`
- Isolation du logger (logger nommé `"swigi"` avec `propagate = False`)

**Exclus :**

- Publication sur PyPI (pas de `setup.py` / `pyproject.toml` pour distribution)
- Refonte de l'API publique (les fonctions internes restent préfixées `_`)
- Ajout de nouvelles dépendances

## 3. Exigences fonctionnelles

| #   | Exigence                                                                              | Priorité |
| --- | ------------------------------------------------------------------------------------- | -------- |
| F1  | `python swigi.py` fonctionne identiquement à l'ancien monofichier                     | MUST     |
| F2  | `python3 -m swigi` fonctionne comme point d'entrée alternatif                         | MUST     |
| F3  | Aucune dépendance externe ajoutée (hidapi reste la seule dépendance requise)          | MUST     |
| F4  | Chaque module est importable individuellement sans effet de bord                      | MUST     |
| F5  | Le logger `"swigi"` utilise `propagate = False` pour éviter les doublons              | MUST     |
| F6  | Les tests unitaires peuvent importer les modules protocole sans matériel HID connecté | SHOULD   |

## 4. Structure du package

```
swigi.py              # Wrapper de compatibilité (point d'entrée racine)
swigi/
├── __init__.py       # Métadonnées du package (__version__, __author__)
├── __main__.py       # Point d'entrée `python3 -m swigi`
├── constants.py      # Constantes HID++, PIDs, feature codes, configuration
├── transport.py      # Abstraction HID : HIDTransport, TransportError
├── hidapi_loader.py  # Chargement cross-platform de la bibliothèque hidapi
├── protocol.py       # Requêtes HID++ 2.0, résolution features, get_current_host
├── discovery.py      # Découverte des périphériques Logitech (DeviceInfo)
├── daemon.py         # Boucle principale : watchdog, polling, gestion événements
├── gui.py            # Notifications macOS, menu bar rumps, préférences
└── main.py           # CLI (argparse), configuration logger, orchestration
```

### Rôle de chaque module

| Module             | Responsabilité                                                             |
| ------------------ | -------------------------------------------------------------------------- |
| `constants.py`     | Définitions HID++ (VID, PIDs, feature codes, SW_ID, tailles messages)      |
| `transport.py`     | Classe `HIDTransport` (read/write/close) et exception `TransportError`     |
| `hidapi_loader.py` | Chargement de `hidapi` via ctypes selon l'OS (Homebrew, DLL, .so)          |
| `protocol.py`      | `hidpp_request`, `resolve_feature`, `send_change_host`, `get_current_host` |
| `discovery.py`     | `find_device` : scan HID, résolution features, construction `DeviceInfo`   |
| `daemon.py`        | `run_daemon` : boucle événementielle, watchdog, reconnexion, sonde souris  |
| `gui.py`           | `notify` (osascript), `SwiGiMenuBar` (rumps), préférences utilisateur      |
| `main.py`          | Point d'entrée `main()` : parsing CLI, setup logger, lancement daemon/GUI  |

### Graphe de dépendances

```
main.py → daemon.py → protocol.py → transport.py → hidapi_loader.py
                    → discovery.py → protocol.py
                    → gui.py
       → gui.py
       → discovery.py
       → constants.py (utilisé par tous les modules)
```

## 5. Compatibilité ascendante

| Aspect               | Avant (monofichier) | Après (package)                      |
| -------------------- | ------------------- | ------------------------------------ |
| Lancement            | `python swigi.py`   | `python swigi.py` ✅ (inchangé)      |
| Lancement alternatif | —                   | `python3 -m swigi` ✅ (nouveau)      |
| Installation macOS   | `install_mac.sh`    | Identique (copie `swigi/` + wrapper) |
| Installation Windows | `setup_win.bat`     | Identique (copie `swigi/` + wrapper) |
| Dépendances          | `hidapi` seul       | `hidapi` seul ✅ (inchangé)          |
| Flags CLI            | `-v`, `--log-file`  | Identiques ✅                        |

## 6. Conformité constitution

| Principe        | Impact     | Mesure                                                            |
| --------------- | ---------- | ----------------------------------------------------------------- |
| Simplicité      | ✅ Positif | Nombre de modules raisonnable (< 15), aucune dépendance ajoutée   |
| Portabilité     | ✅ Neutre  | Aucun changement d'API OS, même code cross-platform               |
| Robustesse      | ✅ Positif | Modules isolés → bugs localisés, logger dédié sans doublons       |
| Non-intrusivité | ✅ Neutre  | Pas de permission supplémentaire, même mode d'exécution           |
| Réactivité      | ✅ Neutre  | Aucun impact sur les timings (polling, fenêtre lecture, watchdog) |

## 7. Notes d'implémentation

- Le wrapper `swigi.py` à la racine fait un simple `from swigi.main import main; sys.exit(main() or 0)`. Il préserve la compatibilité avec les plist launchd et les VBScript Windows existants.
- `__main__.py` permet `python3 -m swigi` — utile pour les environnements où le module est dans le `PYTHONPATH` sans le wrapper.
- Le logger nommé `"swigi"` avec `propagate = False` évite que les messages remontent au `root` logger et produisent des doublons quand un `StreamHandler` est attaché aux deux niveaux.
- Chaque sous-module utilise `logging.getLogger("swigi.<module>")` — ces loggers héritent automatiquement des handlers du logger parent `"swigi"`.
