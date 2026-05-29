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

## Architecture (8 modules principaux)

| Module         | Rôle                                                                          |
| -------------- | ----------------------------------------------------------------------------- |
| `transport.py` | Wrapper hidapi (read/write/close)                                             |
| `protocol.py`  | HID++ 2.0 (send_change_host, get_current_host, get_protocol_version)          |
| `discovery.py` | Enumération + routing (classify_generation, DeviceInfo)                       |
| `daemon.py`    | Orchestrateur dual-path + dispatcher + helpers partagés (_reconnect_keyboard) |
| `path_push.py` | Watcher Gen S : capture notification CHANGE_HOST                              |
| `path_pull.py` | Watcher Legacy : ping watchdog + reconnexion                                  |
| `gui.py`       | Menu bar macOS (rumps) + prefs JSON                                           |
| `constants.py` | Constantes HID++, PIDs, messages                                              |

## Contrainte macOS BT critique

Sur macOS Bluetooth, le kernel peut fermer le handle HID AVANT que la notification CHANGE_HOST soit lisible en userspace. Le code maximise le temps passé dans `hid_read_timeout()` pour capturer cette notification, mais un échec est possible. Si la souris ne suit pas, le probe retente automatiquement (toutes les 2-3s pendant 30s). Au-delà du timeout, l'utilisateur re-appuie sur Easy-Switch.

## Dispatch et envoi différé

Au moment du dispatch, si la souris est disponible → envoi immédiat. Sinon, `last_target_host` est conservé et le probe envoie dès qu'il découvre une souris. Pas de retry automatique — en cas d'échec, l'utilisateur re-appuie sur Easy-Switch.

## Grâce period post-reconnexion clavier

Après reconnexion du clavier (1.5s), les notifications CHANGE_HOST sont ignorées. Le firmware émet des notifications parasites à la reconnexion BLE.
