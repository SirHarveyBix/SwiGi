# Spec : Fiabilité CHANGE_HOST — souris en mouvement

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté — validé par l'utilisateur

---

## 1. Contexte

Quand la souris envoie activement des rapports de mouvement HID, la commande `CHANGE_HOST` était fréquemment perdue. La cause : les rapports de mouvement saturent la file BT (Bluetooth est partiellement half-duplex au niveau L2CAP) et le firmware de la souris traite la commande en retard ou la rate.

**Ce qui ne fonctionne PAS :**
- Bloquer les événements souris OS (`CGEventTap`) — requiert permission Accessibilité, viole P4
- Retries avec délai (50ms) — le buffer se re-remplit entre chaque tentative

## 2. Solution retenue

**Drain du buffer d'entrée + retries back-to-back.**

1. Avant d'envoyer `CHANGE_HOST` : vider le buffer d'entrée HID de la souris via `hid_read_timeout(..., timeout=0)` (non-bloquant). Maximum 8 lectures.
2. Envoyer la commande 3× consécutivement sans délai — la souris reçoit les 3 dans la même fenêtre de scheduling BT.

## 3. Exigences fonctionnelles

| # | Exigence | Priorité |
|---|----------|----------|
| F1 | CHANGE_HOST réussit même quand la souris envoie des données de mouvement | MUST |
| F2 | Latence ajoutée ≤ 5ms (drain non-bloquant) | MUST |
| F3 | Exception sur 1er write = erreur réelle, propagée | MUST |
| F4 | Exception sur retry (2e/3e) = switch réussi, ignorée silencieusement | MUST |

## 4. Implémentation

```python
def _drain_transport(transport, max_reads=8):
    """Vide le buffer HID non-bloquant avant écriture."""
    for _ in range(max_reads):
        try:
            if transport.read(timeout=0) is None:
                break
        except (TransportError, OSError):
            break

def send_change_host(transport, devnumber, feat_idx, target_host):
    _drain_transport(transport)
    # ... build msg ...
    for attempt in range(3):
        try:
            transport.write(msg)
        except (TransportError, OSError):
            if attempt == 0:
                raise   # transport mort avant 1er envoi = vraie erreur
            return      # mort après envoi = switch réussi = succès silencieux
```

**Pourquoi timeout=0 pour le drain :** `hid_read_timeout(dev, buf, len, 0)` est non-bloquant selon la spec hidapi. Retourne immédiatement si aucune donnée disponible.

**Pourquoi back-to-back sans délai :** le BT scheduler voit 3 paquets consécutifs et les groupe dans une même rafale. Avec 50ms de délai, le buffer se re-remplit et l'avantage est perdu.

## 5. Conformité constitution

| Principe | Impact | Mesure |
|----------|--------|--------|
| Simplicité | ✅ Neutre | Stdlib pure, 15 lignes |
| Portabilité | ✅ Positif | hidapi timeout=0 supporté sur les 3 OS |
| Robustesse | ✅ Positif | Switch fiable même en charge BT élevée |
| Non-intrusivité | ✅ Positif | Pas de CGEventTap, pas de permission Accessibilité |
| Réactivité | ✅ Positif | Latence ajoutée ~0ms vs 100ms avant |

## 6. Validation

Confirmé fonctionnel par l'utilisateur sur MX Keys S + MX Vertical, macOS Sequoia, Bluetooth.
