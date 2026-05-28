# Analyse technique — Refonte SwiGi v2

**Date :** 2026-05-27
**Auteur :** Lead Dev (analyse assistée)
**Version :** 1.0.0

---

## 1. Diagnostic de l'architecture actuelle

### 1.1 Métriques

| Module         | Lignes | Responsabilité                                 | Complexité  |
| -------------- | ------ | ---------------------------------------------- | ----------- |
| daemon.py      | ~1200  | Orchestration, sync, reconnexion, heuristiques | TRÈS ÉLEVÉE |
| protocol.py    | 239    | Protocole HID++ 2.0                            | Modérée     |
| discovery.py   | 117    | Détection périphériques                        | Faible      |
| transport.py   | 62     | Wrapper hidapi                                 | Faible      |
| gui.py         | 345    | Menu bar + prefs                               | Modérée     |
| bettermouse.py | 318    | Profils BetterMouse                            | Modérée     |
| main.py        | 269    | Point d'entrée, lifecycle                      | Modérée     |

### 1.2 Bugs identifiés

| ID  | Sévérité | Localisation                           | Description                                                                                                                                                 |
| --- | -------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B1  | CRITIQUE | daemon.py L1060-1080                   | `_SWITCH_COMMIT_WAIT` (350ms) : si le stack BT met >350ms à signaler la déconnexion, le switch est **ignoré silencieusement**. Cause N°1 des switchs ratés. |
| B2  | HAUTE    | daemon.py (global)                     | Race condition : `pending_host`, `pending_source`, `manual_override_until` lus/écrits par 3 threads sans lock.                                              |
| B3  | MOYENNE  | daemon.py L440-450                     | `mice.clear()` dans `_send_to_all_mice` + probe loop en sleep 5s → fenêtre sans suivi souris.                                                               |
| B4  | MOYENNE  | discovery.py L65-115                   | Fuite de handles HID si exception entre ouverture transport et fin de boucle.                                                                               |
| B5  | BASSE    | daemon.py (watchdog + post-disconnect) | Deux blocs de reconnexion dupliqués (~80 lignes chacun) → bugs divergents.                                                                                  |

### 1.3 Sources de complexité inutile

| Mécanisme                                         | Lignes | Pourquoi c'est nocif                                                        |
| ------------------------------------------------- | ------ | --------------------------------------------------------------------------- |
| Pending host + TTL dual (switch 12s / resync 24s) | ~100   | Interactions imprévisibles entre TTL, source, et grace periods              |
| Détection switch manuel souris                    | ~50    | Impossible à deviner fiablement via timing BT (varie 2-15s)                 |
| Manual override cooldown 12s                      | ~30    | Bloque des corrections légitimes                                            |
| Connexions fantômes + suppress_resync             | ~50    | Edge case ultra-rare, complexité disproportionnée                           |
| Mode strict_switch_only                           | ~30    | Preuve que le resync est nocif — on le désactive au lieu de le supprimer    |
| `_SWITCH_COMMIT_WAIT`                             | ~20    | **CAUSE DIRECTE DE BUGS** — attend une déco qui peut ne pas arriver à temps |
| Double drain + 5x write                           | ~20    | Masque un problème architectural                                            |
| Hunt mode / normal mode adaptatif                 | ~40    | Scheduling complexe pour un gain marginal                                   |
| `_resync_pending_host_from_keyboard`              | ~80    | Le clavier qui revient est DÉJÀ sur le bon hôte — pas besoin de resync      |

**Total : ~420 lignes de code défensif dont chaque couche compense les bugs d'une autre.**

### 1.4 Ce qui fonctionne bien (à conserver)

- `transport.py` : propre, minimal, testable
- `hidapi_loader.py` : chargement multi-plateforme correct
- `discovery.py` : logique de filtrage correcte
- `protocol.py` : implémentation HID++ solide
- Notifications macOS (osascript)
- Instance lock atomique (O_CREAT|O_EXCL)
- Auto-reconnexion (le principe est bon)
- BetterMouse (module autonome correct)

---

## 2. Besoin fonctionnel clarifié

### 2.1 P0 — Besoin primaire
>
> Appui Easy-Switch clavier → la souris bascule sur le même hôte.
> Fonctionne systématiquement que j'aie 2 ou 3 machines.

### 2.2 P1 — Vérification (log)
>
> Après le switch, vérifier que la souris est bien sur le bon hôte.
> Log clair : "✓ confirmé" ou "⚠ timeout/échec".
> **Pas de correction automatique agressive.**

### 2.3 P2 — BetterMouse
>
> Transiter la config souris sur tous les Macs automatiquement.
> Quand la souris arrive sur un Mac, appliquer le profil BetterMouse local.

### 2.4 Ce qui n'est PAS un besoin

- ❌ Corriger automatiquement une désync (commande envoyée = fait)
- ❌ Deviner si l'utilisateur a switché manuellement la souris
- ❌ Resynchroniser après reconnexion clavier
- ❌ Fenêtres de grâce, cooldowns, modes stricts

---

## 3. Architecture cible

### 3.1 Principe

**Pipe unidirectionnel** : Clavier notifie → SwiGi relaie → Souris exécute → Log confirme.

### 3.2 Machine à 3 états

```
IDLE ───(switch détecté)───→ SWITCHING ───(commande envoyée)───→ VERIFYING
  ▲                                                                  │
  └─────────────────────(confirmé OU timeout 10s)────────────────────┘
```

- **IDLE** : écoute clavier, probe souris toutes les 3s
- **SWITCHING** : envoie CHANGE_HOST à toutes les souris connues, immédiatement
- **VERIFYING** : au prochain probe, vérifie hôte souris → log résultat → retour IDLE

### 3.3 Règles

1. Pas de pending_host TTL → un simple `last_target_host: int | None`
2. Pas de correction auto → si souris pas sur bon hôte après 10s, log WARNING et clear
3. Pas de switch manuel detection → supprimé
4. Pas de _SWITCH_COMMIT_WAIT → envoi immédiat
5. Reconnexion = un seul helper factorisé
6. BetterMouse = hook post-vérification (après confirmation log)

---

## 4. Verdict sur la stack

| Composant       | Verdict      | Justification                       |
| --------------- | ------------ | ----------------------------------- |
| Python 3        | ✅ GARDER     | Suffisant pour le besoin            |
| hidapi (ctypes) | ✅ GARDER     | Seule lib HID raw portable          |
| threading       | ⚠️ SIMPLIFIER | 2 threads max (1 clavier + 1 probe) |
| rumps           | ✅ GARDER     | Menu bar macOS, optionnel           |
| BetterMouse     | ✅ GARDER     | Besoin validé                       |

**La stack est correcte. Le problème est l'architecture logicielle, pas les outils.**
