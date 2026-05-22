# Spec : Synchronisation garantie des hôtes

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

`send_change_host` est fire-and-forget : la commande est envoyée à la souris mais rien ne confirme qu'elle a été reçue et exécutée. En cas de perte BT partielle, le clavier peut basculer sur l'hôte X pendant que la souris reste sur l'hôte Y. L'utilisateur se retrouve avec les deux périphériques sur des hôtes différents sans aucun signal d'erreur.

**Scénario typique :**

1. Easy-Switch pressé → clavier passe sur hôte 2
2. `send_change_host` envoyé à la souris → paquet perdu (BT saturé)
3. Clavier déconnecté de Mac 1, souris reste sur Mac 1
4. Utilisateur sur Mac 2 avec clavier mais sans souris

---

## 2. Solution : deux filets de sécurité indépendants

### Filet 1 — Vérification immédiate post-switch (300ms)

Après `send_change_host` réussi (premier essai sans exception), SwiGi attend 300ms et tente un ping à la souris :

- **Ping échoue** (TransportError/OSError) → souris déconnectée → switch confirmé ✅
- **Ping réussit** → souris encore présente → switch non exécuté → retry × 3

Chaque retry attend 200ms supplémentaires avant de repiquer. Si après 3 retries la souris n'a toujours pas basculé → notification utilisateur + filet 2 prend le relais.

**Pourquoi ping = déconnexion = succès :** quand la souris reçoit `CHANGE_HOST`, elle bascule sur le nouveau hôte BT et coupe la connexion avec le Mac actuel. Une écriture HID vers un device BT déconnecté lève immédiatement `TransportError`. C'est la seule confirmation disponible sans channel de retour dédié.

### Filet 2 — Resynchronisation au reconnect

Appelée par `_verify_and_sync(kb, mouse, state)` dans 4 contextes :

- Reconnexion clavier (handler post-switch)
- Watchdog reconnect (après 10s sans réponse)
- Sonde périodique souris (toutes les 5s si `state["mouse"] is None`)
- (futur) tout contexte où les deux périphériques viennent d'être trouvés

**Algorithme :**

1. `get_current_host(kb)` → interroge feature `CHANGE_HOST` fn 0 (`getHostInfo`) → `reply[1]` = hôte actuel
2. `get_current_host(mouse)` → idem
3. Si `kb_host == mouse_host` → rien à faire
4. Si différents → `send_change_host(mouse, kb_host)` → souris rejoint l'hôte du clavier
5. `mouse.close()` + `state["mouse"] = None` → souris va déconnecter après la correction, sera redécouverte via sonde périodique

---

## 3. Exigences fonctionnelles

| #   | Exigence                                                                                    | Priorité |
| --- | ------------------------------------------------------------------------------------------- | -------- |
| F1  | Si souris encore connectée 300ms après CHANGE_HOST → retry automatique × 3                  | MUST     |
| F2  | Au reconnect des deux périphériques → vérifier hôtes et corriger si désync                  | MUST     |
| F3  | `get_current_host` utilisé comme source de vérité (pas un état interne)                     | MUST     |
| F4  | Si désync détectée → notification utilisateur                                               | SHOULD   |
| F5  | Correction silencieuse si `get_current_host` retourne None (pas de crash)                   | MUST     |
| F6  | Après correction sync → `state["mouse"] = None` + attente redécouverte via sonde périodique | MUST     |

---

## 4. Implémentation

### `get_current_host`

```python
def get_current_host(transport, devnumber, feat_idx):
    reply = hidpp_request(transport, devnumber, (feat_idx << 8) | 0x00, timeout=500)
    if reply and len(reply) >= 2:
        return reply[1]  # reply[0] = numHosts, reply[1] = currentHost
    return None
```

### `_verify_and_sync`

```python
def _verify_and_sync(kb, mouse, state):
    kb_host = get_current_host(kb.transport, DEVNUMBER_DIRECT, kb.change_host_idx)
    mouse_host = get_current_host(mouse.transport, DEVNUMBER_DIRECT, mouse.change_host_idx)
    if kb_host is None or mouse_host is None:
        return
    if kb_host == mouse_host:
        return
    # Désync → corriger
    send_change_host(mouse.transport, DEVNUMBER_DIRECT, mouse.change_host_idx, kb_host)
    mouse.close()
    state["mouse"] = None
```

### Vérification immédiate (dans `_run_daemon`)

```python
# Après send_change_host réussi :
time.sleep(0.3)  # attendre déconnexion souris
try:
    mouse.transport.write(_PING_MSG)
    # Ping réussi → souris encore là → retry × 3 (200ms entre chaque)
except (TransportError, OSError):
    pass  # Ping échoué → switch confirmé
```

---

## 5. Timings

| Étape                               | Durée         | Impact perçu                                                |
| ----------------------------------- | ------------- | ----------------------------------------------------------- |
| Vérification post-switch (succès)   | +300ms        | Invisible (keyboard déjà déconnecté)                        |
| Retry × 1 (si nécessaire)           | +200ms        | Invisible                                                   |
| Retry × 2                           | +200ms        | Invisible                                                   |
| Retry × 3                           | +200ms        | Invisible                                                   |
| `get_current_host` × 2 au reconnect | ~1000ms total | Pendant la phase de reconnexion, pas sur le chemin critique |

La vérification post-switch se déroule pendant la phase où le clavier se déconnecte. L'utilisateur ne perçoit pas ces 300ms car les deux périphériques sont de toute façon en transition.

---

## 6. Points d'appel de `_verify_and_sync`

| Contexte                    | Condition déclencheur                      |
| --------------------------- | ------------------------------------------ |
| Reconnect handler (clavier) | Après trouver souris en proactif           |
| Watchdog reconnect          | Après trouver kb + mouse                   |
| Sonde périodique souris     | Après redécouverte souris (state was None) |

---

## 7. Conformité constitution

| Principe        | Impact     | Mesure                                                        |
| --------------- | ---------- | ------------------------------------------------------------- |
| Simplicité      | ✅ Neutre  | Stdlib pure, ~30 lignes pour les deux filets                  |
| Portabilité     | ✅ Positif | `get_current_host` = HID++ 2.0 standard, 3 OS                 |
| Robustesse      | ✅ Positif | Désync détectée et corrigée automatiquement                   |
| Non-intrusivité | ✅ Neutre  | Pas de permission supplémentaire                              |
| Réactivité      | ✅ Neutre  | Vérification pendant phase transition, chemin normal inchangé |
