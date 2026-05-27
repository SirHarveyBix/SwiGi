# Tâches : Simplification SwiGi v2

**Date :** 2026-05-27
**Plan de référence :** .specify/plan.md

---

## Tâches

| ID | Titre | Type | Priorité | Dépendances | Statut | Phase |
|----|-------|------|----------|-------------|--------|-------|
| T1 | Extraire helper `_reconnect_keyboard` | refactor | P0 | — | FAIT | 1 |
| T2 | Réécrire daemon.py (pipe unidirectionnel, ~340 lignes) | refactor | P0 | T1 | FAIT | 1 |
| T3 | Simplifier `_watch_keyboard` (supprimer commit wait, envoi immédiat) | refactor | P0 | T2 | FAIT | 1 |
| T4 | Réécrire `_mice_probe_loop` (probe fixe + hunt trigger) | refactor | P0 | T2 | FAIT | 1 |
| T5 | Fix fuite handles `find_all_devices` (try/finally) | fix | P1 | — | FAIT | 2 |
| T6 | Lock unique pour state dict | fix | P1 | T2 | FAIT | 2 |
| T7 | Simplifier `send_change_host` (2 writes + 1 drain) | refactor | P1 | — | FAIT | 2 |
| T8 | Vérification post-switch (log + retry 1× après 5s) | feat | P1 | T4 | FAIT | 2 |
| T9 | Hook BetterMouse post-vérification | feat | P2 | T8 | FAIT | 3 |
| T10 | Transit config BetterMouse multi-Mac (docs README) | feat | P2 | T9 | FAIT | 3 |
| T11 | Réécrire tests daemon (13 tests, _drain_switch/_watch/_probe/run) | refactor | P1 | T2, T3, T4 | FAIT | 4 |
| T12 | Supprimer code mort (daemon_old, test_daemon_old, pending_host) | refactor | P1 | T2 | FAIT | 4 |
| T13 | Mettre à jour GUI (_lock au lieu de _state_lock) | refactor | P2 | T12 | FAIT | 4 |
| T14 | Mettre à jour README (features, troubleshooting, BetterMouse) | docs | P2 | T13 | FAIT | 4 |
| T15 | Logs colorés terminal + emoji | feat | P2 | — | FAIT | — |
| T16 | Installation curl fonctionnelle | feat | P2 | — | FAIT | — |

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
