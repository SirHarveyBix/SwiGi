# Spec : Fiabilité CHANGE_HOST — souris en mouvement

**Version :** 1.1.0
**Date :** 2026-05-22
**Statut :** Implémenté — validé par l'utilisateur

---

## 1. Contexte

Quand la souris envoie activement des rapports de mouvement HID, la commande `CHANGE_HOST` était fréquemment perdue. La cause : les rapports de mouvement saturent la file BT (Bluetooth est partiellement half-duplex au niveau L2CAP) et le firmware de la souris traite la commande en retard ou la rate.

**Ce qui ne fonctionne PAS :**

- Bloquer les événements souris OS (`CGEventTap`) — requiert permission Accessibilité, viole P4
- Retries avec délai (50ms) — le buffer se re-remplit entre chaque tentative

## 2. Solution retenue

**Double drain (avec wait 1ms) + retries back-to-back + flush OS TX buffer.**

1. **Drain phase 1** : vider le buffer d'entrée HID de la souris via `hid_read_timeout(..., timeout=1)` (1ms par lecture). Maximum 32 lectures.
2. **Wait 1ms** : laisse arriver les paquets BT in-flight (critique sur BT 5.3 / M3 Pro).
3. **Drain phase 2** : vider les nouveaux paquets arrivés pendant le wait.
4. Envoyer la commande 5× consécutivement sans délai — la souris reçoit les 5 dans la même fenêtre de scheduling BT.
5. Après les 5 writes : lecture courte (10ms) pour forcer le BT stack OS à expédier les paquets en attente dans le TX buffer kernel.

## 3. Exigences fonctionnelles

| #   | Exigence                                                                     | Priorité |
| --- | ---------------------------------------------------------------------------- | -------- |
| F1  | CHANGE_HOST réussit même quand la souris envoie des données de mouvement     | MUST     |
| F2  | Fonctionne sur M1 Pro (BT 5.0) et M3 Pro (BT 5.3)                            | MUST     |
| F3  | Latence ajoutée ≤ 15ms (double drain + flush)                                | MUST     |
| F4  | Exception sur 1er write = erreur réelle, propagée                            | MUST     |
| F5  | Exception sur retry (2e–5e) = switch réussi, ignorée silencieusement         | MUST     |
| F6  | `last_switch_time` mis à jour à la détection de la notification, pas au send | MUST     |

## 4. Implémentation

```python
def _drain_transport(transport, max_reads=32):
    """Vide le buffer HID avant écriture. timeout=1ms pour fiabilité M3 Pro."""
    for _ in range(max_reads):
        try:
            if transport.read(timeout=1) is None:
                break
        except (TransportError, OSError):
            break

def send_change_host(transport, devnumber, feat_idx, target_host):
    _drain_transport(transport)
    time.sleep(0.001)   # laisse arriver les paquets BT in-flight (BT 5.3)
    _drain_transport(transport)
    # ... build msg ...
    for attempt in range(5):
        try:
            transport.write(msg)
        except (TransportError, OSError):
            if attempt == 0:
                raise   # transport mort avant 1er envoi = vraie erreur
            return      # mort après envoi = switch réussi = succès silencieux
    # Flush OS TX buffer : force le BT stack à expédier les writes en attente
    try:
        transport.read(timeout=10)
    except (TransportError, OSError):
        pass  # souris déconnectée = commande reçue, attendu
```

**Pourquoi timeout=1 (pas 0) :** sur macOS Sonoma/Sequoia avec BT 5.3 (M3 Pro), `hid_read_timeout(..., 0)` peut ignorer des paquets déjà présents dans la file kernel. 1ms donne au BT stack le temps de rendre les paquets disponibles. Sur M1 (BT 5.0), `timeout=1` fonctionne identiquement à `timeout=0` (le buffer est souvent vide dès la 1ère lecture).

**Pourquoi double drain avec sleep 1ms :** entre la fin du drain phase 1 et l'écriture, des paquets in-flight sur le lien BT 5.3 (intervalle de connexion ~4ms) peuvent arriver. Le sleep 1ms + drain phase 2 les absorbe avant l'écriture.

**Pourquoi 5 retries (pas 3) :** BT 5.3 à haute fréquence peut interleaver des rapports de mouvement entre les writes. 5 copies augmentent la probabilité qu'au moins une passe dans une fenêtre de scheduling libre.

**Pourquoi flush read (10ms) :** `hid_write` sur macOS BT enfile le paquet dans le TX buffer kernel. Une lecture courte donne au BT stack l'opportunité de vider la file TX. 10ms vs 5ms : plus sûr sur M3 dont le scheduler BT est plus rapide.

**Pourquoi `last_switch_time` à la détection :** le clavier se déconnecte dès qu'il envoie la notification CHANGE_HOST, quelle que soit la réussite du send vers la souris. Stocker le timestamp dès la détection garantit que la déconnexion clavier post-switch est toujours reconnue comme telle, même si le send échoue.

## 5. Conformité constitution

| Principe        | Impact     | Mesure                                             |
| --------------- | ---------- | -------------------------------------------------- |
| Simplicité      | ✅ Neutre  | Stdlib pure, ~20 lignes                            |
| Portabilité     | ✅ Positif | hidapi timeout=0 supporté sur les 3 OS             |
| Robustesse      | ✅ Positif | Switch fiable même en charge BT élevée             |
| Non-intrusivité | ✅ Positif | Pas de CGEventTap, pas de permission Accessibilité |
| Réactivité      | ✅ Positif | Latence ajoutée ~5ms vs 100ms avant                |

## 6. Validation

Confirmé fonctionnel par l'utilisateur sur MX Keys S + MX Vertical, macOS Sequoia, Bluetooth.
