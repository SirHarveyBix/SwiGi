# Plan : Simplification radicale SwiGi v2

**Date :** 2026-05-27
**Spec de référence :** .specify/analysis.md

---

## Pourquoi cette refonte

### Le problème constaté

SwiGi ne fonctionne pas de manière fiable. Les symptômes :

- **Des switchs sont ignorés** : on appuie sur Easy-Switch, rien ne se passe. Il faut réappuyer.
- **Comportement imprévisible** : parfois la souris bascule toute seule sans raison, parfois elle refuse de suivre.
- **Instabilité avec 3 machines** : ça marche à peu près avec 2, ça devient chaotique avec 3.
- **Impossible à débugger** : les logs sont un mur de texte technique incompréhensible (pending_host, TTL, resync, phantom, override...).

### La cause racine

Le projet a accumulé des **couches de protection** qui se combattent entre elles :

1. On a ajouté une vérification post-switch → ça crée des faux négatifs → on ajoute une correction automatique
2. La correction auto re-switch la souris quand on ne veut pas → on ajoute une détection de "switch manuel"
3. La détection de switch manuel a des faux positifs → on ajoute un cooldown
4. La reconnexion clavier déclenche des corrections → on ajoute un mode "strict"
5. Le mode strict ne couvre pas tous les cas → on ajoute des TTL, des fenêtres de grâce...

**Résultat : 1200 lignes de daemon qui se battent contre elles-mêmes.**

Le bug le plus grave (`_SWITCH_COMMIT_WAIT`) est emblématique : le code **attend que le clavier se déconnecte** pour confirmer qu'un switch a eu lieu. Si le Bluetooth met plus de 350ms à signaler la déco (fréquent sur BT 5.3), le switch est ignoré. C'est un design fondamentalement cassé.

### Ce qu'on veut à la place

Un outil **simple et déterministe** :

- J'appuie sur Easy-Switch → la souris suit. **Toujours.** Avec 2 ou 3 machines.
- Si ça a marché → je le vois dans les logs (confirmation claire).
- Si ça n'a pas marché → je le vois aussi (warning clair), et je réappuie.
- Pas de "corrections automatiques" qui changent de Mac sans que j'aie rien demandé.
- Pas de comportement différent selon qu'il y a 2 ou 3 machines.

### Ce qu'on garde

- ✅ La stack Python + hidapi (elle est correcte)
- ✅ La reconnexion automatique (mais simplifiée)
- ✅ La vérification post-switch (**en log seulement**, pas de correction)
- ✅ BetterMouse (transit de config multi-Mac)
- ✅ L'icône menu bar macOS
- ✅ Le support multi-clavier

### Ce qu'on supprime

- ❌ `_SWITCH_COMMIT_WAIT` — cause directe des switchs ignorés
- ❌ Pending host + TTL — remplacé par un simple `last_target_host`
- ❌ Correction automatique de désync — source d'instabilité
- ❌ Détection de switch manuel souris — impossible à faire fiablement
- ❌ Cooldown / override — patches sur patches
- ❌ Resync from keyboard — le clavier qui revient est déjà sur le bon hôte
- ❌ Mode strict vs permissif — preuve qu'on ne sait plus quel comportement est correct
- ❌ Connexions fantômes / phantom window — edge case traité par un simple debounce

---

## Constitution Check

- [x] Principe 1 — Simplicité : suppression de ~800 lignes, aucune nouvelle dépendance
- [x] Principe 2 — Portabilité : aucun changement aux couches transport/hidapi
- [x] Principe 3 — Robustesse : reconnexion conservée mais factorisée en un seul helper
- [x] Principe 4 — Non-intrusivité : inchangé (mode non-exclusif conservé)
- [x] Principe 5 — Réactivité : suppression de _SWITCH_COMMIT_WAIT → latence réduite
- [x] Principe 6 — Clarté : machine à états explicite, noms clairs, plus d'abréviations

---

## Phase 1 — Réécriture du daemon (P0 — bloque tout)

### 1.1 Extraire helper `_reconnect_keyboard`

**Objectif :** Factoriser les 2 blocs de reconnexion dupliqués.

**Signature :**

```python
def _reconnect_keyboard(
    product_id: int,
    stop_event: threading.Event,
    initial_delay: float = 0.5,
    max_delay: float = 5.0,
    stability_seconds: float = 0.5,
) -> DeviceInfo | None:
```

**Logique :**

1. Boucle avec backoff exponentiel (delay × 1.5, max 5s)
2. `find_keyboard_by_product_id` → si trouvé, attendre `stability_seconds`
3. Vérifier stabilité via ping → si OK, retourner
4. Si `stop_event.is_set()` → retourner None

### 1.2 Réécrire `daemon.py` — machine à 3 états

**État partagé (protégé par un seul lock) :**

```python
@dataclasses.dataclass
class DaemonState:
    lock: threading.Lock
    keyboard_name: str | None = None
    keyboard_ok: bool = False
    mice_names: list[str] = field(default_factory=list)
    last_target_host: int | None = None  # remplace tout le système pending
    last_switch_time: float = 0.0
    switches_count: int = 0
```

**Supprimé :**

- `pending_host` / `pending_source`
- `manual_override_until`
- `suppress_resync_until`
- `last_change_host_had_mice`
- `strict_switch_only`
- `debug_raw_packets`
- `_SWITCH_COMMIT_WAIT`
- Tout le système de TTL

### 1.3 Simplifier `_watch_keyboard`

**Garder :**

- Ping throttlé (100ms)
- Lecture fenêtre 100ms pour notifications CHANGE_HOST (swid=0)
- Debounce 1s sur même target
- Reconnexion via helper factorisé (1.1)
- Drain simple (10 reads max) au moment de la déconnexion

**Supprimer :**

- Drain complexe (60 reads + err_streak + none_streak)
- `_SWITCH_COMMIT_WAIT` → sur switch détecté, poster IMMÉDIATEMENT dans la queue
- Phantom window / suppress_resync
- `_resync_pending_host_from_keyboard`

**Sur switch détecté :**

```python
event_queue.put(_SwitchEvent(target_host, keyboard.name, keyboard.product_id))
# C'est tout. Pas de commit wait.
```

### 1.4 Réécrire `_mice_probe_loop`

**Probe dual-speed avec hunt trigger :**

- Mode normal : probe toutes les 3s (`_PROBE_INTERVAL`)
- Mode rapide : probe toutes les 1s pendant 15s après un switch (`_PROBE_FAST_INTERVAL`, `_PROBE_FAST_DURATION`)
- Déclenché par `hunt_trigger.set()` après chaque switch ou reconnexion clavier

**Logique par cycle :**

1. `find_all_devices(MOUSE)` → mettre à jour liste (ajouter nouvelles, retirer mortes)
2. Si `last_target_host` est set → vérifier via `get_current_host()`
3. Si confirmé → log INFO "✓" + appliquer BetterMouse + clear target
4. Si timeout (10s) → log WARNING "⚠" + clear target
5. **Pas de retry CHANGE_HOST** — la commande a été envoyée dans le dispatcher

**Vérification post-switch (log only) :**

```python
current = get_current_host(mouse.transport, ...)
if current == last_target_host:
    log.info("✓ %s confirmée sur hôte %d", mouse.name, current + 1)
    _apply_better_mouse(mouse.name)
elif current is not None:
    log.warning("⚠ %s sur hôte %d, attendu %d", mouse.name, current + 1, last_target_host + 1)
# Dans tous les cas : clear last_target_host, retour IDLE
```

### 1.5 Boucle principale (dispatcher)

```python
while not stop_event.is_set():
    event = event_queue.get(timeout=1.0)
    if isinstance(event, _SwitchEvent):
        # Immédiat : envoyer à toutes les souris
        _send_to_all_mice(mice_list, event.target_host, state, mouse_lock)
        state.switches_count += 1
        hunt_trigger.set()  # probe rapide pour vérification
```

Pas de commit wait. Pas de vérification si le clavier s'est déconnecté.

---

## Phase 2 — Fiabilité (parallélisable avec Phase 3)

### 2.1 Fix fuite de handles dans `discovery.py`

```python
def find_all_devices(device_type_wanted: int) -> list[DeviceInfo]:
    # ... enumeration ...
    results = []
    for ... in candidates:
        transport = None
        try:
            transport = HIDTransport(path, product_id)
            # ... feature resolution ...
            results.append(DeviceInfo(...))
            transport = None  # ownership transferred
        except Exception:
            pass
        finally:
            if transport is not None:
                transport.close()
    return results
```

### 2.2 Lock unique pour state

Un seul `threading.Lock()` protège toutes les clés de `DaemonState`.
Granularité grossière acceptable car les opérations sont rapides (pas d'I/O sous lock).

### 2.3 Simplifier `send_change_host`

```python
def send_change_host(transport, device_number, feature_index, target_host):
    _drain_transport(transport, max_reads=8)
    message = _build_message(device_number, request_id, parameters)
    transport.write(message)  # single write — le firmware acquitte ou déconnecte
```

**Pas de double write** — un second envoi CHANGE_HOST pendant que le firmware traite le premier peut créer une race condition interne au périphérique.
**Pas de flush read** — un `read()` après `write()` n'accélère pas l'envoi sur macOS BT ; il ajoute de la latence inutile.

### 2.4 Vérification post-switch (log only, pas de retry)

**Timing :** Au probe suivant (3s max après switch).
**Logique :**

- `get_current_host()` sur chaque souris
- Si hôte == target → log INFO "✓" + appliquer BetterMouse si activé
- Si hôte != target → log WARNING "⚠"
- Si lecture impossible → log DEBUG (souris déconnectée = elle a basculé)
- Clear `last_target_host` dans tous les cas

**Strictement aucun retry, aucune correction automatique.** L'info est dans les logs pour diagnostic. Si la souris n'a pas suivi, l'utilisateur rappuie sur Easy-Switch.

---

## Phase 3 — BetterMouse (parallélisable avec Phase 2)

### 3.1 Hook post-vérification

Déclenché dans `_mice_probe_loop` APRÈS la vérification de Phase 2.4 :

```python
if confirmed_on_target:
    _apply_better_mouse_profile_if_needed(mouse.name)
```

### 3.2 Transit config multi-Mac

**Concept :** Chaque Mac a son profil BetterMouse local (exporté via le menu).
Quand la souris arrive, SwiGi applique le profil local automatiquement.

**Implémentation :**

- Garder `bettermouse.py` existant (correct et testé)
- Garder le hook `_apply_better_mouse_profile_if_needed`
- Simplifier : appeler uniquement quand une souris est nouvellement détectée
- Pas besoin de réseau : chaque Mac est autonome

---

## Phase 4 — Nettoyage et tests

### 4.1 Réécrire tests daemon

Tests simplifiés — pas besoin de `_fast_probe` ni de hacks de timing :

- `test_switch_sends_change_host` : switch → commande envoyée immédiatement
- `test_switch_no_mice_retries` : souris absente → retry au probe suivant
- `test_verification_logs_success` : après switch, probe confirme hôte OK → log
- `test_verification_logs_failure` : après switch, hôte différent → log warning
- `test_reconnection_no_side_effects` : reconnexion clavier → pas de switch parasite
- `test_bettermouse_applied_on_confirm` : profil appliqué après confirmation

### 4.2 Supprimer code mort

Constantes à supprimer :

- `_PENDING_HOST_TTL_SWITCH`, `_PENDING_HOST_TTL_RESYNC`
- `_MANUAL_SWITCH_GRACE`, `_MANUAL_OVERRIDE_COOLDOWN`
- `_ENABLE_KEYBOARD_RESYNC_PENDING`, `_RESYNC_AFTER_SWITCH_WINDOW`
- `_SWITCH_COMMIT_WAIT`
- `_KEYBOARD_PHANTOM_WINDOW`
- `_MOUSE_HUNT_INTERVAL`, `_MOUSE_HUNT_WINDOW`

Fonctions à supprimer :

- `_resync_pending_host_from_keyboard`
- `_check_and_apply_pending_host`

### 4.3 GUI — simplification

Garder :

- Statut clavier/souris (✅/❌)
- Compteur basculements
- Toggle notifications
- Toggle souris suit clavier
- Section BetterMouse

Supprimer :

- Tout élément référençant les mécanismes supprimés

---

## Risques

| Risque                                           | Probabilité | Impact | Mitigation                                                  |
| ------------------------------------------------ | ----------- | ------ | ----------------------------------------------------------- |
| Cas où la correction auto était utile            | Moyen       | Moyen  | Log WARNING visible → si fréquent, ajouter 1 retry simple   |
| BT macOS ne livre pas la notification avant déco | Faible      | Élevé  | Drain simple (10 reads) au moment de la déco capture ce cas |
| Multi-clavier : switch simultané                 | Très faible | Faible | Premier arrivé gagne (queue FIFO)                           |
| Probe 3s trop lent pour trouver la souris        | Faible      | Moyen  | Trigger immédiat après switch (hunt_trigger event)          |

---

## Métriques de succès

| Métrique                | Actuel                         | Cible                                    |
| ----------------------- | ------------------------------ | ---------------------------------------- |
| Lignes daemon.py        | ~1200                          | ≤ 250                                    |
| Latence switch → souris | Variable (350ms+ commit wait)  | < 200ms                                  |
| Switchs ignorés         | Fréquent (_SWITCH_COMMIT_WAIT) | Zéro                                     |
| Log par switch          | Bruit (debug_raw_packets)      | 2 lignes : "envoyé" + "confirmé/timeout" |
| Constantes de timing    | 15+                            | ≤ 5                                      |
| Race conditions         | 3+ identifiées                 | 0 (lock unique)                          |

---

## Phase 5 — Corrections post-implémentation v2

### Contexte

Après l'implémentation des Phases 1-4, un audit du code résultant a identifié des anomalies introduites par des contradictions dans les specs originales (T7/T8) et des valeurs de temporisation sous-optimales.

### 5.1 Fix `send_change_host` — single write

**Problème :** Le code implémente un double write (`for attempt in range(2): transport.write(...)`) où les deux writes s'exécutent même sans erreur. Un double CHANGE_HOST peut confondre le firmware Logitech (race condition interne).

**Fix :** Single write. Si le write échoue → exception propagée. Si le write réussit → terminé.

### 5.2 Supprimer flush `read(timeout=10)` post-write

**Problème :** Le `transport.read(timeout=10)` après les writes ne "force" rien sur macOS BT — c'est un no-op qui ajoute 10ms de latence.

**Fix :** Supprimer.

### 5.3 Supprimer retry agressif, conserver envoi différé

**Problème :** Le code renvoyait CHANGE_HOST après 5s si la souris n'était pas sur le bon hôte. Un retry agressif peut interférer avec un switch lent mais réussi.

**Distinction critique :**

- **Retry** (supprimé) = le dispatcher a envoyé (`sent > 0`) mais la souris n'a pas changé → renvoyer risque une race condition firmware
- **Envoi différé** (conservé) = le dispatcher n'a PAS PU envoyer (`sent = 0`, aucune souris disponible) → quand le probe trouve la souris, il envoie CHANGE_HOST une seule fois puis clear le target

**Fix :** Le probe envoie CHANGE_HOST uniquement si `last_target_host` est set ET la souris est sur le mauvais hôte. Après envoi → `state["last_target_host"] = None` (pas de boucle).

### 5.4 Optimiser temporisations

| Constante         | Valeur actuelle | Nouvelle valeur | Justification                                                        |
| ----------------- | --------------- | --------------- | -------------------------------------------------------------------- |
| `_READ_WINDOW`    | 0.18 (180ms)    | 0.10 (100ms)    | Constitution ≥80ms ; 100ms suffisant, réduit latence                 |
| `_STABILITY_WAIT` | 2.0 (2s)        | 0.5 (500ms)     | Constitution interdit >1s chemin critique ; ping valide la stabilité |
| `_DEBOUNCE`       | 2.0 (2s)        | 1.0 (1s)        | 2s bloque des switchs rapides légitimes                              |

### 5.5 Fix menu bar — double bouton Quitter

**Problème :** `rumps.App` ajoute automatiquement un bouton "Quit" en anglais. Le code ajoute aussi `_rumps.MenuItem("Quitter", ...)`. Résultat : deux boutons quit.

**Fix :** `super().__init__("⌨️", quit_button=None)` pour supprimer le "Quit" par défaut.

### 5.6 Supprimer `_drain_transport` dans `get_device_name`

**Problème :** Le drain entre chaque chunk de nom ajoute ~50ms à la discovery. Le drain déjà présent dans `find_all_devices` avant `resolve_feature(CHANGE_HOST)` est suffisant.

**Fix :** Supprimer les appels `_drain_transport(transport, max_reads=8)` dans `get_device_name`.

### 5.7 Mécanisme PULL — rapatriement souris sur reconnexion clavier

**Problème :** Sur macOS BT, la notification CHANGE_HOST du clavier est souvent perdue lors de la déconnexion. Le kernel ferme le handle HID avant que la notification puisse être lue. Résultat : le PUSH (notification → dispatch) échoue ~50% du temps.

**Solution :** Modèle PULL complémentaire au PUSH :

1. Au démarrage de la surveillance clavier : `get_current_host(keyboard)` → `state["this_mac_host"]`
2. À chaque reconnexion du clavier (= il revient sur ce Mac) : `state["last_target_host"] = this_mac_host`
3. Le probe fait le reste : trouve la souris, vérifie si elle est sur le bon hôte, envoie CHANGE_HOST si nécessaire

**Pourquoi ça marche :**

- La souris MX Master 4 maintient des connexions BLE multiples (elle reste joignable même quand active sur un autre Mac)
- Le PULL ne dépend PAS de la notification volatile : il se déclenche sur la reconnexion clavier (événement fiable)
- Si SwiGi tourne sur les deux Macs : chaque Mac tire la souris vers lui quand le clavier arrive → la souris suit toujours

**Aucun conflit avec le PUSH :** Si la notification EST capturée (PUSH réussit), le probe confirme avec "✓". Si la notification est perdue (PUSH échoue), le PULL rattrape au moment de la reconnexion.

---

## Conventions de log

| Événement                    | Format                                             | Niveau |
| ---------------------------- | -------------------------------------------------- | ------ |
| Clavier surveillé            | `⌨️  [Nom] Surveillance démarrée (hôte N)`          | INFO   |
| Clavier déconnecté           | `🔌 [Nom] Déconnecté`                               | INFO   |
| Clavier reconnecté           | `🔄 ⌨️ [Nom] Reconnecté`                             | INFO   |
| Souris découverte (nouvelle) | `🖱️  Souris : Nom (PID=0xXXXX)`                     | INFO   |
| Souris déconnectée           | `🔌 🖱️ [Nom] Déconnectée (switch en cours/manuel ?)` | INFO   |
| Souris reconnectée           | `🔄 🖱️ [Nom] Reconnectée` + `Hôte actuel : N`        | INFO   |
| Switch détecté               | `★ [Clavier] Easy-Switch → hôte N`                 | INFO   |
| Envoi immédiat               | `⚡ Souris → hôte N`                                | INFO   |
| Envoi différé (probe)        | `→ Souris sur hôte X, envoi vers hôte Y`           | INFO   |
| PULL (reconnexion clavier)   | `🔁 Clavier revenu → ramener souris sur hôte N`     | INFO   |
| Vérification OK              | `✓ Souris sur hôte N — confirmé`                   | INFO   |
| Timeout vérification         | `⚠ Timeout vérification hôte N — abandon`          | WARN   |
| Aucune souris au dispatch    | `⚠ Aucune souris — retry au prochain probe`        | WARN   |

---

## Clarifications fonctionnelles

| Comportement         | Règle                                                                                                                                                           |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Souris en mouvement  | La souris suit le clavier **même si elle bouge** — CHANGE_HOST est envoyé sur l'interface HID++ (usage page 0xFF00), indépendante des movement reports (0x0001) |
| Switch souris manuel | Le clavier **ne suit PAS** — pipe strictement unidirectionnel : clavier → souris                                                                                |
| Rappui Easy-Switch   | Chaque pression = envoi immédiat CHANGE_HOST → la souris revient                                                                                                |
| Toggle "Souris suit" | Désactive l'envoi de CHANGE_HOST aux souris ; le clavier switch seul                                                                                            |
| Bouton Quitter       | Empêche le crash recovery loop de relancer automatiquement ; seule une exécution manuelle relance SwiGi                                                         |
| Restart au boot      | Non affecté par Quit — launchd/login item relance au prochain démarrage système                                                                                 |
