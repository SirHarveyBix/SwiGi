<!--
SYNC IMPACT REPORT
==================
Version change: (none) → 1.0.0
Type: Initial constitution — création depuis le contexte du projet

Principes définis:
  1. Simplicité (single-file, zéro dépendance métier)
  2. Portabilité (macOS / Windows / Linux)
  3. Robustesse (reconnexion, watchdog)
  4. Non-intrusivité (coexistence avec Logi Options+, pas de root)
  5. Réactivité (latence minimale Easy-Switch)

Templates créés:
  ✅ .specify/templates/plan-template.md
  ✅ .specify/templates/spec-template.md
  ✅ .specify/templates/tasks-template.md

Follow-up TODOs:
  - TODO(RATIFICATION_DATE): confirmer la date d'adoption officielle si différente de 2026-05-22
  - TODO(MAINTAINERS): ajouter co-mainteneurs éventuels
-->

# Constitution du Projet SwiGi

**Version:** 1.0.0
**Date de ratification:** 2026-05-22
**Dernière modification:** 2026-05-22
**Mainteneur principal:** SirHarveyBix (gui.lefort.17@gmail.com)

---

## 1. Objet et portée

SwiGi est un daemon Python monofile qui synchronise la touche Easy-Switch entre un clavier et une souris Logitech via Bluetooth, sans receveur USB, sans Logi Options+ et sans contrainte réseau. Il implémente le protocole HID++ 2.0 (feature CHANGE_HOST `0x1814`) directement via la bibliothèque hidapi.

Cette constitution définit les règles non-négociables qui gouvernent l'évolution du projet. Toute contribution, modification ou refactorisation DOIT respecter ces principes.

---

## 2. Principes fondamentaux

### Principe 1 — Simplicité

**Règle :** SwiGi DOIT rester un fichier Python unique (`swigi.py`). La seule dépendance externe autorisée est `hidapi`. Aucun framework, aucun gestionnaire de paquets (pip, poetry, etc.) ne DOIT être requis pour exécuter le projet.

**Rationale :** La friction zéro à l'installation est la proposition de valeur principale. Toute abstraction supplémentaire qui exigerait une installation de dépendances Python invalide cet objectif.

**Tests de conformité :**
- `python swigi.py` fonctionne après `brew install hidapi` / placement de `hidapi.dll` sans aucune autre commande.
- `wc -l swigi.py` reste à un niveau raisonnable (< 700 lignes).

### Principe 2 — Portabilité

**Règle :** SwiGi DOIT fonctionner sur macOS (≥ Monterey), Windows 10/11, et Linux (distributions majeures avec udev) sans modification du code source. Les chemins de chargement de hidapi et les comportements spécifiques par OS DOIVENT être encapsulés dans `_load_hidapi()` et des blocs `if _SYSTEM ==`.

**Rationale :** Le projet est utilisé par des utilisateurs sur les trois plateformes. Une divergence de comportement non documentée constitue un bug, pas une fonctionnalité.

**Tests de conformité :**
- CI/CD ou tests manuels documentés sur les trois OS.
- Aucun import conditionnel qui provoquerait un `ImportError` sur une plateforme supportée.

### Principe 3 — Robustesse

**Règle :** SwiGi DOIT se reconnecter automatiquement après toute déconnexion Bluetooth (clavier ou souris), sans intervention utilisateur. Le watchdog DOIT détecter l'absence de réponse HID++ et forcer une reconnexion dans un délai ≤ 15 secondes. Aucune exception non gérée ne DOIT faire crasher le daemon silencieusement.

**Rationale :** Un daemon qui nécessite un redémarrage manuel perd sa valeur principale. Le Bluetooth est intrinsèquement instable ; la résilience est une exigence fonctionnelle, pas un bonus.

**Tests de conformité :**
- Déconnecter/reconnecter le clavier BT : le daemon se reconnecte en < 60s.
- Watchdog se déclenche après 10s sans réponse et tente reconnexion des deux périphériques.
- Arrêt propre via `Ctrl+C` et `SIGTERM` (systemd/launchd).

### Principe 4 — Non-intrusivité

**Règle :** SwiGi DOIT fonctionner en mode non-exclusif sur macOS (appel à `hid_darwin_set_open_exclusive(0)`). Il NE DOIT PAS requérir de privilèges root/admin au runtime (les règles udev Linux sont une configuration initiale, pas un prérequis permanent). Il NE DOIT PAS interférer avec Logi Options+ ou tout autre logiciel Logitech.

**Rationale :** Les utilisateurs qui ont déjà un logiciel Logitech installé doivent pouvoir utiliser SwiGi en parallèle. Forcer un choix exclusif est inacceptable.

**Tests de conformité :**
- Logi Options+ ouvert et actif : SwiGi démarre sans erreur et fonctionne correctement.
- Pas de `sudo` requis dans les instructions de démarrage normal.

### Principe 5 — Réactivité

**Règle :** La latence entre la pression de Easy-Switch et le basculement de la souris DOIT être inférieure à 300ms dans des conditions normales. Le polling DOIT utiliser un intervalle ≤ 100ms. La fenêtre de lecture des réponses HID++ DOIT être ≥ 80ms pour capturer les notifications asynchrones.

**Rationale :** Une latence perceptible (> 500ms) dégrade l'expérience utilisateur et remet en question l'utilité du tool. La réactivité est une exigence de qualité de service, pas une optimisation facultative.

**Tests de conformité :**
- `time.sleep(0.01)` dans la boucle principale (10ms).
- Fenêtre de lecture à 80ms (`deadline = time.time() + 0.08`).
- Pas de `time.sleep()` > 1s dans le chemin critique de traitement d'un événement.

---

## 3. Gouvernance

### 3.1 Procédure d'amendement

1. Ouvrir une issue GitHub décrivant le principe à modifier et la justification.
2. Discussion publique minimale de 48h pour les modifications MINOR, 7 jours pour MAJOR.
3. Mise à jour de cette constitution avec `speckit-constitution`.
4. Commit de type `docs: amend constitution to vX.Y.Z (...)`.

### 3.2 Politique de versionnement

Suit la sémantique suivante :
- **MAJOR** : suppression ou redéfinition incompatible d'un principe.
- **MINOR** : ajout d'un nouveau principe ou extension matérielle d'un existant.
- **PATCH** : clarifications, formulation, corrections orthographiques.

### 3.3 Révision de conformité

Chaque Pull Request modifiant `swigi.py` DOIT inclure une vérification mentale des 5 principes. En cas de violation intentionnelle (nécessité technique documentée), une note explicite DOIT figurer dans le message de commit ou la PR description.

### 3.4 Compatibilité avec les outils de spécification

Ce projet utilise le framework **SpecKit** pour la documentation technique :
- `.specify/memory/constitution.md` — ce fichier.
- `.specify/templates/` — gabarits pour plans, specs, et tâches.
- Skill `speckit-constitution` pour les amendements.
- Skill `speckit-specify` pour les spécifications de features.

---

## 4. Référence technique

| Élément | Valeur |
|---------|--------|
| Protocole | HID++ 2.0 |
| Feature CHANGE_HOST | `0x1814` |
| SW_ID (identifiant SwiGi) | `0x0A` |
| VID Logitech | `0x046D` |
| Bolt PID | `0xC548` |
| Unifying PIDs | `0xC52B`, `0xC532` |
| Intervalle polling | 10ms |
| Fenêtre lecture | 80ms |
| Timeout watchdog | 10s |
| Python requis | 3.10+ |
