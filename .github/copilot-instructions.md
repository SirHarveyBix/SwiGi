# SwiGi — Instructions pour agents IA

## Projet

Daemon Python synchronisant Easy-Switch Logitech (clavier → souris) via HID++ 2.0 / hidapi. macOS + Windows + Linux. Pas de Logi Options+, pas de réseau.

## Documentation obligatoire

Avant toute modification, lire dans cet ordre :
1. `.specify/memory/constitution.md` — 6 principes NON-NÉGOCIABLES
2. `.specify/plan.md` — architecture actuelle et décisions techniques
3. `.specify/analysis.md` — diagnostic et bugs résolus

## Conventions code

- Noms explicites, pas d'abréviations (`keyboard` pas `kb`, `device` pas `dev`)
- Pas d'over-engineering : pas de retry, pas de correction auto, pas de TTL
- Single write pour CHANGE_HOST, pas de flush read
- Tests : `python3 -m pytest tests/ -x -q` + `python3 -m ruff check swigi/ tests/`
- Lancer les 2 avant de déclarer une tâche terminée

## Architecture (5 modules)

| Module         | Rôle                                                                     |
| -------------- | ------------------------------------------------------------------------ |
| `transport.py` | Wrapper hidapi (read/write/close)                                        |
| `protocol.py`  | HID++ 2.0 (send_change_host, get_current_host, hidpp_request)            |
| `discovery.py` | Enumération périphériques Logitech                                       |
| `daemon.py`    | Pipe unidirectionnel : keyboard watch → event queue → dispatcher → mouse |
| `gui.py`       | Menu bar macOS (rumps) + prefs JSON                                      |

## Contrainte macOS BT critique

Sur macOS Bluetooth, le kernel peut fermer le handle HID AVANT que la notification CHANGE_HOST soit lisible en userspace. Le code maximise le temps passé dans `hid_read_timeout()` pour capturer cette notification, mais un échec est possible. Le mécanisme PULL (keyboard reconnect → ramener souris) sert de fallback.
