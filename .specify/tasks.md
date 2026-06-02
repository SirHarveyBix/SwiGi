# Tâches : Simplification SwiGi v2

**Date :** 2026-05-27
**Plan de référence :** .specify/plan.md

---

## Tâches

| ID  | Titre                                                                                                             | Type     | Priorité | Dépendances | Statut                                              | Phase |
| --- | ----------------------------------------------------------------------------------------------------------------- | -------- | -------- | ----------- | --------------------------------------------------- | ----- |
| T1  | Extraire helper `_reconnect_keyboard`                                                                             | refactor | P0       | —           | ✔️                                                  | 1     |
| T2  | Réécrire daemon.py (pipe unidirectionnel, ~340 lignes)                                                            | refactor | P0       | T1          | ✔️                                                  | 1     |
| T3  | Simplifier `_watch_keyboard` (supprimer commit wait, envoi immédiat)                                              | refactor | P0       | T2          | ✔️                                                  | 1     |
| T4  | Réécrire `_mice_probe_loop` (probe fixe + hunt trigger)                                                           | refactor | P0       | T2          | ✔️                                                  | 1     |
| T5  | Fix fuite handles `find_all_devices` (try/finally)                                                                | fix      | P1       | —           | ✔️                                                  | 2     |
| T6  | Lock unique pour state dict                                                                                       | fix      | P1       | T2          | ✔️                                                  | 2     |
| T7  | Simplifier `send_change_host` (1 write, drain max_reads=8, pas de flush)                                          | refactor | P1       | —           | ✔️                                                  | 2     |
| T8  | Vérification post-switch (log only — pas de retry)                                                                | feat     | P1       | T4          | ✔️                                                  | 2     |
| T9  | Hook BetterMouse post-vérification                                                                                | feat     | P2       | T8          | ✔️                                                  | 3     |
| T10 | Transit config BetterMouse multi-Mac (docs README)                                                                | feat     | P2       | T9          | ✔️                                                  | 3     |
| T11 | Réécrire tests daemon (13 tests, \_drain_switch/\_watch/\_probe/run)                                              | refactor | P1       | T2, T3, T4  | ✔️                                                  | 4     |
| T12 | Supprimer code mort (daemon_old, test_daemon_old, pending_host)                                                   | refactor | P1       | T2          | ✔️                                                  | 4     |
| T13 | Mettre à jour GUI (\_lock au lieu de_state_lock)                                                                  | refactor | P2       | T12         | ✔️                                                  | 4     |
| T14 | Mettre à jour README (features, troubleshooting, BetterMouse)                                                     | docs     | P2       | T13         | ✔️                                                  | 4     |
| T15 | Logs colorés terminal + emoji                                                                                     | feat     | P2       | —           | ✔️                                                  | —     |
| T16 | Installation curl fonctionnelle                                                                                   | feat     | P2       | —           | ✔️                                                  | —     |
| T17 | Fix `send_change_host` : single write (supprimer double write)                                                    | fix      | P0       | T7          | ✔️                                                  | 5     |
| T18 | Supprimer flush `read(10)` post-write dans `send_change_host`                                                     | fix      | P0       | T17         | ✔️                                                  | 5     |
| T19 | Réduire `_READ_WINDOW` de 0.18 à 0.10                                                                             | perf     | P1       | —           | ✔️                                                  | 5     |
| T20 | Réduire `_STABILITY_WAIT` de 2.0 à 0.5                                                                            | perf     | P1       | —           | ✔️                                                  | 5     |
| T21 | Réduire `_DEBOUNCE` de 2.0 à 1.0                                                                                  | perf     | P1       | —           | ✔️                                                  | 5     |
| T22 | Supprimer retry switch dans `_mice_probe_loop`                                                                    | fix      | P0       | T8          | ✔️                                                  | 5     |
| T23 | Supprimer `_drain_transport` dans `get_device_name`                                                               | perf     | P2       | —           | ✔️                                                  | 5     |
| T24 | Fix double bouton Quitter (`quit_button=None`)                                                                    | fix      | P1       | —           | ✔️                                                  | 5     |
| T25 | Vérifier que Quit empêche auto-restart mais pas restart au boot                                                   | qa       | P1       | T24         | ✔️                                                  | 5     |
| T26 | Review finale post-implémentation                                                                                 | qa       | P0       | T17-T25     | ✔️                                                  | 5     |
| T27 | Ajouter `get_protocol_version()` dans protocol.py                                                                 | feat     | P0       | —           | ✔️                                                  | 6     |
| T28 | Ajouter `classify_generation()` dans discovery.py (routing fusionné)                                              | feat     | P0       | T27         | ✔️                                                  | 6     |
| T29 | Ajouter champ `generation` à DeviceInfo dans discovery.py                                                         | refactor | P0       | T28         | ✔️                                                  | 6     |
| T30 | Créer `swigi/path_push.py` (watch_keyboard_push)                                                                  | feat     | P0       | T29         | ✔️                                                  | 6     |
| T31 | Créer `swigi/path_pull.py` (watch_keyboard_pull)                                                                  | feat     | P0       | T29         | ❌ abandonné Phase 10 — legacy non supporté         | 6     |
| T32 | Réécrire daemon.py comme orchestrateur (spawn par generation)                                                     | refactor | P0       | T30, T31    | ✔️                                                  | 6     |
| T33 | Créer `tests/test_routing.py`                                                                                     | test     | P1       | T28         | ❌ abandonné — classify_generation() non implémenté | 6     |
| T34 | Créer `tests/test_path_push.py`                                                                                   | test     | P1       | T30         | ✔️                                                  | 6     |
| T35 | Créer `tests/test_path_pull.py`                                                                                   | test     | P1       | T31         | ❌ abandonné avec T31                               | 6     |
| T36 | Mettre à jour `tests/test_daemon.py` (dispatcher unifié, mixed keyboards)                                         | test     | P1       | T32         | ✔️                                                  | 6     |
| T37 | Nettoyage : supprimer \_watch_keyboard monolithique, \_pull_mouse_on_reconnect                                    | refactor | P2       | T32         | ✔️                                                  | 6     |
| T38 | Validation finale Phase 6 (pytest + ruff + constitution check)                                                    | qa       | P0       | T33-T37     | ✔️                                                  | 6     |
| T39 | Fix `path_pull.py` : \_SwitchEvent + get_current_host post-reconnect + watchdog correct                           | fix      | P0       | T31         | ❌ abandonné avec T31                               | 7     |
| T40 | Fix `daemon.py` : last_switch_sent + last_switch_time + hunt_trigger.set() dans dispatcher                        | fix      | P0       | T32         | ✔️                                                  | 7     |
| T41 | Fix `_mice_probe_loop` : TTL \_VERIFY_TIMEOUT + deferred send si sent=0 + log ✓/⚠                                 | fix      | P0       | T40         | ✔️                                                  | 7     |
| T42 | Fix `path_push.py` : raw[3] sw_id=0 filtre + hunt_trigger.set() + drain_switch sur TransportError read            | fix      | P1       | T30         | ✔️                                                  | 7     |
| T43 | Tests phase 7 : test_deferred_send_when_no_mouse, test_verify_timeout_clears_state, test_no_pingpong_on_reconnect | test     | P1       | T40-T41     | ✔️                                                  | 7     |
| T44 | Validation finale Phase 7 (pytest + ruff)                                                                         | qa       | P0       | T43         | ✔️                                                  | 7     |
| T45 | Réduire `_RECONNECT_GRACE` 1.5→0.2s (bloquait switchs rapides post-reconnect)                                     | fix      | P1       | —           | ✔️                                                  | 7     |
| T46 | Libérer `mouse_lock` avant HID I/O dans dispatcher (évite starvation probe)                                       | fix      | P2       | —           | ✔️                                                  | 7     |
| T47 | Fix `_drain_switch` : `continue`→`break` sur read vide (élimine blocage 2s sur buffer vide)                       | fix      | P0       | —           | ✔️                                                  | 7     |
| T48 | `_reconnect_keyboard` : drain 50→10 reads, ping timeout 500→200ms                                                 | perf     | P1       | —           | ✔️                                                  | 7     |
| T49 | Startup `get_current_host` timeout 500→200ms dans PUSH et PULL (log diagnostic seulement)                         | perf     | P1       | —           | ✔️                                                  | 7     |
| T50 | Supprimer `log.info("━" * 40)` dans `path_push.py` (bruit, code mort)                                             | cleanup  | P2       | —           | ✔️                                                  | 7     |
| T51 | Fix ping-pong stale notifications : filtre `last_reconnect_target` + `this_mac_host` dans `path_push.py`          | fix      | P0       | —           | ✔️                                                  | 7     |

---

## Critères d'acceptation

- [x] `daemon.py` compact (454L dont 84 blancs/commentaires — vs 1200L avant)
- [x] Aucun switch ignoré silencieusement (envoi immédiat, pas de commit wait)
- [x] Log clair pour chaque switch : "★ Easy-Switch → hôte X" + "✓ confirmé" ou "⚠ timeout"
- [x] Tests passent sans hacks de timing (148 tests, 0 échecs)
- [x] BetterMouse appliqué après confirmation switch
- [x] Fonctionne identiquement avec 2 ou 3 machines (pipe unidirectionnel)
- [x] Constitution respectée (6 principes)
- [x] Linter ruff : 0 erreurs
- [x] Aucune référence résiduelle au vieux daemon (pending_host,\_SWITCH_COMMIT_WAIT, daemon_old)
- [x] `send_change_host` : single write, pas de double envoi, pas de flush read
- [x] Aucun retry automatique dans le probe loop (log only)
- [x] `_READ_WINDOW` ≤ 0.10, `_STABILITY_WAIT` ≤ 0.5, `_DEBOUNCE` ≤ 1.0
- [x] Un seul bouton Quitter dans le menu bar
- [x] Quit empêche le crash recovery mais pas le restart au boot
- [ ] La souris suit le clavier même en mouvement (vérifié fonctionnellement)

---

## Critères d'acceptation Phase 6 (dual-path PUSH/PULL)

- [x] `classify_generation()` dans discovery.py retourne "push" ou "pull"
- [x] `swigi/path_push.py` existe avec `watch_keyboard_push()` fonctionnel
- [x] `swigi/path_pull.py` existe avec `watch_keyboard_pull()` fonctionnel
- [x] `DeviceInfo` a un champ `generation: str` ("push" ou "pull")
- [x] `daemon.py` spawn le bon watcher selon `keyboard.generation`
- [x] Tests routing : ≥ 3 cas (gen_s, legacy, fallback)
- [x] Tests path_push : ≥ 5 cas (notification, debounce, drain, reconnect, watchdog)
- [x] Tests path_pull : ≥ 4 cas (reconnect, no-read, watchdog, stop)
- [x] `ls swigi/*.py | wc -l` ≤ 14
- [x] `python3 -m pytest tests/ -x -q` : 0 échecs
- [x] `python3 -m ruff check swigi/ tests/` : 0 erreurs
- [x] Debounce dispatcher : même target < 1s → second event droppé
- [x] Zéro redondance : \_reconnect_keyboard et \_post_pull_event centralisés dans daemon.py
