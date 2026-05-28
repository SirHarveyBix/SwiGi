# Tâches : Simplification SwiGi v2

**Date :** 2026-05-27
**Plan de référence :** .specify/plan.md

---

## Tâches

| ID  | Titre                                                                    | Type     | Priorité | Dépendances | Statut | Phase |
| --- | ------------------------------------------------------------------------ | -------- | -------- | ----------- | ------ | ----- |
| T1  | Extraire helper `_reconnect_keyboard`                                    | refactor | P0       | —           | ✔️      | 1     |
| T2  | Réécrire daemon.py (pipe unidirectionnel, ~340 lignes)                   | refactor | P0       | T1          | ✔️      | 1     |
| T3  | Simplifier `_watch_keyboard` (supprimer commit wait, envoi immédiat)     | refactor | P0       | T2          | ✔️      | 1     |
| T4  | Réécrire `_mice_probe_loop` (probe fixe + hunt trigger)                  | refactor | P0       | T2          | ✔️      | 1     |
| T5  | Fix fuite handles `find_all_devices` (try/finally)                       | fix      | P1       | —           | ✔️      | 2     |
| T6  | Lock unique pour state dict                                              | fix      | P1       | T2          | ✔️      | 2     |
| T7  | Simplifier `send_change_host` (1 write, drain max_reads=8, pas de flush) | refactor | P1       | —           | ✔️      | 2     |
| T8  | Vérification post-switch (log only — pas de retry)                       | feat     | P1       | T4          | ✔️      | 2     |
| T9  | Hook BetterMouse post-vérification                                       | feat     | P2       | T8          | ✔️      | 3     |
| T10 | Transit config BetterMouse multi-Mac (docs README)                       | feat     | P2       | T9          | ✔️      | 3     |
| T11 | Réécrire tests daemon (13 tests, _drain_switch/_watch/_probe/run)        | refactor | P1       | T2, T3, T4  | ✔️      | 4     |
| T12 | Supprimer code mort (daemon_old, test_daemon_old, pending_host)          | refactor | P1       | T2          | ✔️      | 4     |
| T13 | Mettre à jour GUI (_lock au lieu de _state_lock)                         | refactor | P2       | T12         | ✔️      | 4     |
| T14 | Mettre à jour README (features, troubleshooting, BetterMouse)            | docs     | P2       | T13         | ✔️      | 4     |
| T15 | Logs colorés terminal + emoji                                            | feat     | P2       | —           | ✔️      | —     |
| T16 | Installation curl fonctionnelle                                          | feat     | P2       | —           | ✔️      | —     |
| T17 | Fix `send_change_host` : single write (supprimer double write)           | fix      | P0       | T7          | ✔️      | 5     |
| T18 | Supprimer flush `read(10)` post-write dans `send_change_host`            | fix      | P0       | T17         | ✔️      | 5     |
| T19 | Réduire `_READ_WINDOW` de 0.18 à 0.10                                    | perf     | P1       | —           | ✔️      | 5     |
| T20 | Réduire `_STABILITY_WAIT` de 2.0 à 0.5                                   | perf     | P1       | —           | ✔️      | 5     |
| T21 | Réduire `_DEBOUNCE` de 2.0 à 1.0                                         | perf     | P1       | —           | ✔️      | 5     |
| T22 | Supprimer retry switch dans `_mice_probe_loop`                           | fix      | P0       | T8          | ✔️      | 5     |
| T23 | Supprimer `_drain_transport` dans `get_device_name`                      | perf     | P2       | —           | ✔️      | 5     |
| T24 | Fix double bouton Quitter (`quit_button=None`)                           | fix      | P1       | —           | ✔️      | 5     |
| T25 | Vérifier que Quit empêche auto-restart mais pas restart au boot          | qa       | P1       | T24         | ✔️      | 5     |
| T26 | Review finale post-implémentation                                        | qa       | P0       | T17-T25     | ✔️      | 5     |

---

## Critères d'acceptation

- [x] `daemon.py` compact (427L dont 84 blancs/commentaires — vs 1200L avant)
- [x] Aucun switch ignoré silencieusement (envoi immédiat, pas de commit wait)
- [x] Log clair pour chaque switch : "★ Easy-Switch → hôte X" + "✓ confirmé" ou "⚠ timeout"
- [x] Tests passent sans hacks de timing (106 tests, 0 échecs)
- [x] BetterMouse appliqué après confirmation switch
- [x] Fonctionne identiquement avec 2 ou 3 machines (pipe unidirectionnel)
- [x] Constitution respectée (6 principes)
- [x] Linter ruff : 0 erreurs
- [x] Aucune référence résiduelle au vieux daemon (pending_host, _SWITCH_COMMIT_WAIT, daemon_old)
- [x] `send_change_host` : single write, pas de double envoi, pas de flush read
- [x] Aucun retry automatique dans le probe loop (log only)
- [x] `_READ_WINDOW` ≤ 0.10, `_STABILITY_WAIT` ≤ 0.5, `_DEBOUNCE` ≤ 1.0
- [x] Un seul bouton Quitter dans le menu bar
- [x] Quit empêche le crash recovery mais pas le restart au boot
- [ ] La souris suit le clavier même en mouvement (vérifié fonctionnellement)
