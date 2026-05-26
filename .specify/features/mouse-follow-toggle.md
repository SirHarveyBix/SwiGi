# Spec : Toggle suivi souris

**Version :** 1.0.0
**Date :** 2026-05-26
**Statut :** Implémenté

---

## 1. Contexte

L'utilisateur souhaite pouvoir désactiver le suivi automatique de la souris lorsque le clavier bascule via Easy-Switch. Cela permet d'utiliser le clavier sur un Mac différent de celui de la souris si besoin.

**Comportement clé :**
- Seul le switch du **clavier** peut déclencher le suivi de la souris (jamais l'inverse)
- Le switch de la souris via Easy-Switch ne déclenche PAS le switch du clavier
- Le suivi peut être activé/désactivé via une checkbox dans le menu bar

---

## 2. Périmètre

**Inclus :**

- Préférence `mouse_follow` (booléen, défaut : `true`) persistée dans `~/.swigi_prefs.json`
- Checkbox "Souris suit le clavier" dans le menu bar macOS
- Quand désactivé : aucun `CHANGE_HOST` envoyé à la souris lors d'un switch clavier
- Quand désactivé : `pending_host` et corrections de désync sont inhibés

**Exclus :**

- Suivi souris → clavier (n'existe pas et ne doit pas exister)
- Configuration par hôte/profil du suivi (v2 éventuel)

---

## 3. Exigences fonctionnelles

| #   | Exigence                                                                       | Priorité |
| --- | ------------------------------------------------------------------------------ | -------- |
| F1  | Checkbox visible dans le menu bar entre le compteur et les notifications       | MUST     |
| F2  | Cochée par défaut (suivi actif = comportement historique)                       | MUST     |
| F3  | Décochée → `CHANGE_HOST` n'est PAS envoyé à la souris lors d'un switch clavier | MUST     |
| F4  | Décochée → `pending_host` est effacé (pas de correction différée)              | MUST     |
| F5  | Changement en temps réel (pas besoin de relancer SwiGi)                        | MUST     |
| F6  | Préférence persistée dans `~/.swigi_prefs.json`                                | MUST     |
| F7  | Switch clavier toujours compté dans le compteur même si suivi désactivé        | SHOULD   |

---

## 4. Points de garde dans le code

| Fonction                            | Comportement si `mouse_follow = False`            |
| ----------------------------------- | ------------------------------------------------- |
| `run_daemon` (event loop)           | Ne pas appeler `_send_to_all_mice`                |
| `_check_and_apply_pending_host`     | Effacer `pending_host`, retourner False           |
| `_resync_pending_host_from_keyboard`| Effacer `pending_host`, ne pas lire l'hôte clavier|

---

## 5. Conformité constitution

| Principe        | Impact     | Mesure                                                        |
| --------------- | ---------- | ------------------------------------------------------------- |
| Simplicité      | ✅ Neutre  | Un booléen dans prefs, une checkbox, 3 gardes dans daemon.py  |
| Portabilité     | ✅ Neutre  | Préférence JSON standard, menu bar = macOS only (fallback OK) |
| Robustesse      | ✅ Neutre  | Pas de risque de boucle ou crash ajouté                       |
| Non-intrusivité | ✅ Positif | L'utilisateur a le contrôle total                             |
| Réactivité      | ✅ Neutre  | Aucun impact sur le polling ou la latence                     |

---

## 6. Plan de test

- [x] `mouse_follow=False` → `_send_to_all_mice` non appelé
- [x] `mouse_follow=False` → `_check_and_apply_pending_host` efface pending, retourne False
- [x] `mouse_follow=False` → `_resync_pending_host_from_keyboard` efface pending
- [x] `mouse_follow=True` → comportement normal (CHANGE_HOST envoyé)
- [x] Valeur par défaut = True (rétro-compatible)
