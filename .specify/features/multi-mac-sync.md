# Spec : Synchronisation multi-Mac (3 Macs, 2 claviers, 1 souris)

**Version :** 1.0.0
**Date :** 2026-05-26
**Statut :** Implémenté — validé en production

---

## 1. Contexte

Environnement prod : 3 Macs (macOS 13+), 2 claviers MX Keys PID=0xB35B, 1 souris MX Master 4 PID=0xB042.

SwiGi tourne sur les 3 Macs simultanément. Chaque instance surveille les périphériques localement connectés via Bluetooth HID++ 2.0. Il n'y a aucune communication réseau entre les instances.

---

## 2. Architecture multi-Mac

```
[Mac 1 — hôte 0]          [Mac 2 — hôte 1]          [Mac 3 — hôte 2]
  SwiGi instance 1           SwiGi instance 2           SwiGi instance 3
  surveille :                surveille :                surveille :
    - clavier BT (hôte 0)     - clavier BT (hôte 1)     - clavier BT (hôte 2)
    - souris BT (si locale)   - souris BT (si locale)   - souris BT (si locale)
```

**Invariant clé :** un Mac ne peut envoyer CHANGE_HOST qu'aux périphériques actuellement connectés à lui via BT. Quand la souris est sur Mac2, Mac1 ne peut pas la commander.

---

## 3. Scénario type : Mac1 → Mac2 → Mac3 → Mac1

1. Clavier sur Mac1 (hôte 0) : Easy-Switch pressé → hôte 1
2. SwiGi Mac1 détecte CHANGE_HOST(1) depuis le clavier
3. SwiGi Mac1 envoie CHANGE_HOST(1) à la souris
4. Souris bascule sur Mac2 (hôte 1)
5. SwiGi Mac2 : probe détecte la souris (reconnect BT)
6. SwiGi Mac2 vérifie pending_host via get_current_host (feature 0x1814)
7. Si sync OK → RAS. Si désync → correction automatique.

---

## 4. Architecture multi-clavier

Deux claviers MX Keys (même PID=0xB35B) connectés au même Mac :

- **Un thread par clavier** (`_watch_keyboard` × 2)
- **Déduplication PID** dans `find_all_devices` : un seul handle par PID (le mieux scoré)
- **Exception** : 2 claviers différents avec le même PID ne sont PAS supportés simultanément (libhidapi double-free). En pratique, les MX Keys sur hôtes différents n'apparaissent pas simultanément sur le même Mac.

---

## 5. Synchronisation pending_host

`state["pending_host"]` = `(target_host, deadline)` mémorisé après chaque switch.

Quand la souris se reconnecte au Mac de destination :
1. `_check_and_apply_pending_host` compare `get_current_host()` avec `target_host`
2. Si désync → envoie CHANGE_HOST correctif
3. Si sync → efface `pending_host`
4. TTL = 60s → abandon si la souris ne revient pas dans ce délai

Cas particulier — reconnexion clavier après switch :
- `_resync_pending_host_from_keyboard` recale `pending_host` sur l'hôte RÉEL du clavier
- Évite les fausses corrections si le clavier revient sur un autre hôte

---

## 6. Cas limites et edge cases découverts

| Cas | Comportement |
|-----|-------------|
| Clavier déconnecté 30s, revenu sur même hôte | Resync pending_host → souris suit correctement |
| Clavier revenu sur hôte différent (switch manuel) | pending_host recalé → pas de fausse correction |
| Deux switches rapides (A→B→A en < 2s) | second pending_host écrase le premier → OK |
| Souris disparue pendant le TTL | pending_host expiré après 60s → abandon |
| macOS BT retourne réponses paddées (32 octets) | MSG_LENGTHS check accepte len >= (fix 2026-05-26) |
| 2 souris connectées simultanément | _send_to_all_mice envoie à toutes, probe reçoit toutes |

---

## 7. Conformité constitution

| Principe | Impact | Mesure |
|----------|--------|--------|
| Simplicité | ✅ | Pas de réseau, pas de coordination inter-Macs |
| Portabilité | ✅ | Fonctionne aussi Linux/Windows (mêmes principes) |
| Robustesse | ✅ | Reconnexion auto, pending_host avec TTL, backoff exp. |
| Non-intrusivité | ✅ | Mode non-exclusif, coexiste avec Logi Options+ |
| Réactivité | ✅ | Probe hunt 1s×30s post-reconnect, no busy-wait |
