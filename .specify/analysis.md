# Analyse technique — SwiGi v2 (état post-Phase 6)

**Date :** 2026-06-01 (mis à jour Phase 9)
**Version :** 2.1.0

---

## 1. État après refonte Phases 1–6

### 1.1 Métriques actuelles

| Module         | Lignes | Responsabilité                           | Complexité |
| -------------- | ------ | ---------------------------------------- | ---------- |
| daemon.py      | ~341   | Orchestration, dispatch, probe souris    | Faible     |
| path_push.py   | ~220   | Watcher Gen S (notification CHANGE_HOST) | Modérée    |
| protocol.py    | ~211   | HID++ 2.0 (request, send_change_host)    | Modérée    |
| discovery.py   | ~165   | Détection périphériques (DeviceInfo)     | Modérée    |
| transport.py   | 62     | Wrapper hidapi                           | Faible     |
| gui.py         | ~345   | Menu bar + prefs                         | Modérée    |
| bettermouse.py | ~318   | Profils BetterMouse                      | Modérée    |
| main.py        | ~269   | Point d'entrée, lifecycle                | Modérée    |

Refonte réussie : daemon.py est passé de ~1200 à ~341 lignes.

### 1.2 Ce qui a été corrigé (Phases 1–6)

- ✅ `_SWITCH_COMMIT_WAIT` supprimé — envoi immédiat
- ✅ `pending_host` / TTL / manual_override supprimés
- ✅ Correction automatique désync supprimée
- ✅ Reconnexion factorisée en helper unique (`_reconnect_keyboard`)
- ✅ Path PUSH (Gen S) isolé dans `path_push.py`
- ✅ `send_change_host` : single write, drain avant envoi
- ✅ Fuite de handles `find_all_devices` corrigée
- ✅ BetterMouse appliqué sur `new_mice + reconnected_mice`
- ✅ Filtre `sw_id=0` sur notifications CHANGE_HOST (`raw[3] & 0x0F == 0x00`)
- ✅ Double bouton Quitter supprimé

---

## 2. Bugs identifiés dans le code actuel (post-Phase 6)

### 2.1 Table des bugs (état 2026-06-01)

| ID    | Sévérité | Localisation                      | Description                                                                       | Statut            |
| ----- | -------- | --------------------------------- | --------------------------------------------------------------------------------- | ----------------- |
| B-C1  | CRITIQUE | `daemon.py` dispatcher + probe    | Pas de deferred send : `sent == 0` → switch perdu définitivement.                 | ✅ T40+T41        |
| B-C2  | HAUTE    | `daemon.py` dispatcher            | `mouse_lock` tenu pendant HID I/O → bloque probe loop.                            | ✅ T46            |
| B-C3  | HAUTE    | `path_push.py`                    | `_RECONNECT_GRACE` trop agressive → switchs rapides post-reconnect ignorés.       | ✅ T45 (→0.2s)    |
| B-C4  | MOYEN    | `daemon.py` `_mice_probe_loop`    | Pas de vérification post-switch (log ✓/⚠).                                        | ✅ T41            |
| B-C5  | MOYEN    | `tasks.md`                        | Statuts tâches désynchronisés.                                                    | ✅ corrigé        |
| B-C6  | FAIBLE   | `daemon.py` `_mice_probe_loop`    | `state["mouse"]`/`state["mice"]` écrits sans lock — race bénigne GUI only.        | 🔲 accepté        |
| B-C7  | FAIBLE   | `daemon.py` dispatcher            | `state["switches"] += 1` même si `sent == 0` — compteur légèrement trompeur.      | 🔲 accepté        |
| B-C8  | CRITIQUE | `path_push.py` `_drain_switch`    | `continue` sur read vide → 10 × 200ms = 2s de blocage sur buffer vide.            | ✅ T47 (→break)   |
| B-C9  | HAUTE    | `daemon.py` `_reconnect_keyboard` | Drain 50 reads + ping 500ms → reconnect inutilement lent.                         | ✅ T48 (10/200ms) |
| B-C10 | MOYEN    | `path_push.py`                    | Startup `get_current_host` timeout 500ms pour un log — latence démarrage watcher. | ✅ T49 (→200ms)   |
| B-C11 | FAIBLE   | `path_push.py`                    | `log.info("━" * 40)` — code mort, bruit log.                                      | ✅ T50 (supprimé) |
| B-C12 | CRITIQUE | `path_push.py` read window        | Notifications BT stale redelivrées 1-3s après reconnect → ping-pong.              | ✅ T51            |
| B-C13 | CRITIQUE | `path_push.py` `reconnect_time`   | `reconnect_time=0.0` → filtre 3 jamais actif sur Mac receveur (fresh connect).    | ✅ T52+T53        |

### 2.2 Cause principale de "souris ne suit pas" — RÉSOLU

**Scénario (résolu) :**

1. Easy-Switch → `_SwitchEvent(target=1, "push")`
2. Dispatcher : souris en transition BT → `TransportError` → `sent = 0`
3. **Avant** : fin. Switch perdu.
4. **Après** : `state["last_target_host"] = 1`, `state["last_switch_sent"] = False`
5. Probe loop (1s, via `hunt_trigger`) : trouve souris, `get_current_host` → mauvais hôte → `send_change_host` différé → log "⚡ différé"
6. `state["last_target_host"] = None` — terminé

TTL `_VERIFY_TIMEOUT = 30s` empêche l'envoi stale si l'utilisateur a switché manuellement entre-temps.

### 2.3 Cause de "ping-pong" (post-Phase 8)

**Clavier Gen S (PUSH, ex: MX Keys Mini 0xB369) :**

**Bug B-C13 résolu (T52+T53) :** `reconnect_time = 0.0` rendait le filtre 3 inactif sur Mac receveur (fresh connect). Firmware Logitech BLE bufferise la dernière notification et la re-livre 0.5–3s après connexion. Sur Mac 3 (receveur), `last_reconnect_target = -1` → condition `target == -1` jamais vraie → notification stale (target = ancien hôte) passait.

**Fix :** `reconnect_time = time.time()` au démarrage + filtre 3 simplifié (temps seul). Toute notification dans les 3s post-connexion est droppée. Le drain-on-disconnect sur Mac source capture les vrais switchs.

**Principes :**

- Mac source (perd le clavier) → fire via drain-on-disconnect → SOURCE DE VÉRITÉ
- Mac receveur (reçoit le clavier) → stale window 3s → DROP toutes notifications
- Pas de communication réseau inter-instances → chaque Mac est défensivement sceptique

---

## 3. Phase 7 — toutes tâches complètes

T39–T51 : ✅ tous résolus. Voir `tasks.md` pour le détail.

---

## 4. Architecture actuelle — ce qui fonctionne bien

- `transport.py` : propre, minimal, testable ✅
- `hidapi_loader.py` : chargement multi-plateforme correct ✅
- `discovery.py` : filtrage correct ✅
- `protocol.py` : HID++ 2.0 solide ✅
- Notifications macOS ✅
- Instance lock atomique ✅
- Auto-reconnexion (principe correct, helper factorisé) ✅
- BetterMouse (module autonome) ✅
- Path PUSH (Gen S) avec notifications CHANGE_HOST ✅
- Debounce 1s pour doubles notifications ✅
- `send_change_host` : drain + single write ✅

---

## 5. État fonctionnel final (post-Phase 10)

Tous les bugs critiques, hauts et moyens sont résolus.

**Phase 10 — Simplification anti-stale + fixes concurrence :**

- `_RECONNECT_GRACE` et `_RECONNECT_STALE_WINDOW` supprimés — détection stale via ping actif 150ms uniquement
- `last_candidate_target` supprimé — mécanisme de fallback éliminé
- `_is_stale_notification()` extrait comme helper clair
- `_drain_switch` : `break` → `continue` sur paquets courts (ne plus rater CHANGE_HOST après paquet court)
- `transport.close()` : pattern swap-then-free (thread-safe, évite double-free libhidapi)
- `state["_lock"]` préservé entre les restarts du daemon
- `state["switches"]`, `last_target_host`, `last_switch_time` protégés par `state["_lock"]`
- `state["mice"]` / `state["mouse"]` écrits sous `state["_lock"]` (cohérence GUI)
- `find_all_devices` dans `_mice_probe_loop` protégé par `try/except` (thread ne meurt plus silencieusement)
- Cleanup handles dans `_daemon_loop` : toujours fermer avant de redécouvrir
- `discovery.py` retry CHANGE_HOST : rejeter device si retry timeout ou même index
- `gui.py` : `prefs.get()` sous `_prefs_lock` dans `_rebuild_profile_menu`

**Phase 9 — Simplification dispatcher + probe :**

- `get_current_host` supprimé du probe loop (élimine 200ms de blocage HID par cycle)
- `last_switch_sent` supprimé : `last_target_host=None` si envoi réussi, `last_target_host=X` si échec
- BetterMouse appliqué immédiatement dans le dispatcher (plus d'attente confirmation probe)
- Probe loop : envoi différé uniquement, pas de vérification

**Constantes de timing actuelles :**

| Constante                 | Valeur | Rôle                                                                    |
| ------------------------- | ------ | ----------------------------------------------------------------------- |
| `_PROBE_INTERVAL`         | 3.0s   | Probe normale souris                                                    |
| `_PROBE_FAST_INTERVAL`    | 1.0s   | Probe rapide post-switch (15s)                                          |
| `_DISPATCHER_DEBOUNCE`    | 1.0s   | Anti-double event même cible                                            |
| `_STABILITY_WAIT`         | 0.5s   | Attente post-découverte BT avant ping                                   |
| `_VERIFY_TIMEOUT`         | 5.0s   | TTL last_target_host (réduit : évite rappel souris après switch manuel) |
| `_STALE_PING_TIMEOUT`     | 100ms  | Timeout ping anti-stale (BLE RTT ≈ 15-30ms, marge x3)                   |
| `_RECONNECT_STALE_WINDOW` | 2.0s   | Fenêtre anti-stale post-reconnect ; expirée dès premier stale détecté   |
| `_KEYBOARD_WAIT_INTERVAL` | 1.0s   | Polling détection clavier (main.py)                                     |
| `_PING_INTERVAL`          | 0.5s   | Ping clavier PUSH                                                       |
| `_READ_WINDOW`            | 0.5s   | Fenêtre lecture notifications PUSH                                      |
| `_DEBOUNCE`               | 1.0s   | Anti-double switch même cible (PUSH)                                    |
| `_WATCHDOG_TIMEOUT`       | 10.0s  | Reconnexion si pas de réponse                                           |

---

## 6. Bugs de l'ancien code (Phases 1–6) — résolus, conservés pour référence

| ID  | Sévérité | Description                                            | Statut    |
| --- | -------- | ------------------------------------------------------ | --------- |
| B1  | CRITIQUE | `_SWITCH_COMMIT_WAIT` 350ms ignorait les switchs lents | ✅ Résolu |
| B2  | HAUTE    | Race condition `pending_host` sans lock                | ✅ Résolu |
| B3  | MOYENNE  | `mice.clear()` + probe 5s → fenêtre sans suivi         | ✅ Résolu |
| B4  | MOYENNE  | Fuite handles HID si exception dans `find_all_devices` | ✅ Résolu |
| B5  | BASSE    | Deux blocs reconnexion dupliqués (~80 lignes chacun)   | ✅ Résolu |
