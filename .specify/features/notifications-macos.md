# Spec : Notifications macOS — événements périphériques

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

L'utilisateur ne voit les logs SwiGi que s'il a une fenêtre Terminal ouverte. Quand SwiGi tourne en arrière-plan (autostart launchd), les déconnexions/reconnexions BT sont invisibles. Les notifications système macOS donnent un retour visuel immédiat sans interaction utilisateur.

## 2. Périmètre

**Inclus :**

- Notification à la connexion initiale du clavier
- Notification à la connexion initiale de la souris
- Notification à la déconnexion du clavier (perte BT)
- Notification à la reconnexion du clavier
- Notification à la reconnexion de la souris (reconnexion proactive post-disconnect clavier)

**Exclus :**

- Notification à chaque basculement Easy-Switch (trop fréquent, agaçant)
- Notifications sur Windows / Linux (hors périmètre — APIs différentes)
- Notifications riches (icône, actions) — nécessiterait PyObjC, viole Principe 1

## 3. Exigences fonctionnelles

| #   | Exigence                                                                    | Priorité |
| --- | --------------------------------------------------------------------------- | -------- |
| F1  | Notification macOS via `osascript` à chaque événement connect/disconnect    | MUST     |
| F2  | Silencieux sur Windows et Linux (no-op)                                     | MUST     |
| F3  | Échec osascript ne provoque pas de crash (try/except)                       | MUST     |
| F4  | Titre fixe « SwiGi », sous-titre indique le type (« Clavier » / « Souris ») | SHOULD   |

## 4. Implémentation (dans `swigi/gui.py`)

```python
def notify(message: str, subtitle: str = "") -> None:
    """Notification macOS via osascript. No-op si désactivé ou hors Darwin."""
    if SYSTEM != "Darwin" or not prefs.get("notifications", True):
        return

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{_esc(message)}" with title "SwiGi"'
    if subtitle:
        script += f' subtitle "{_esc(subtitle)}"'
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
```

La fonction vérifie les préférences utilisateur (`prefs.get('notifications', True)`) — désactivable via le menu bar macOS.

`_esc` filtre les caractères d'échappement pour éviter l'injection dans la commande `osascript`.

Événements déclencheurs :

- `notify(f"{kb.name} connecté", "Clavier")` — démarrage
- `notify(f"{mouse.name} connectée", "Souris")` — démarrage
- `notify(f"{kb.name} déconnecté", "Clavier")` — perte BT
- `notify(f"{kb.name} reconnecté", "Clavier")` — retour BT
- `notify(f"{mouse.name} reconnectée", "Souris")` — reconnexion proactive

## 5. Conformité constitution

| Principe        | Impact     | Mesure                                                                              |
| --------------- | ---------- | ----------------------------------------------------------------------------------- |
| Simplicité      | ✅ Neutre  | `subprocess` stdlib, pas de nouvelle dépendance                                     |
| Portabilité     | ✅ Positif | Guard `_SYSTEM == "Darwin"`, no-op ailleurs                                         |
| Robustesse      | ✅ Positif | Feedback visuel sur les événements de reconnexion                                   |
| Non-intrusivité | ✅ Neutre  | Notifications système standard, pas de popup bloquant                               |
| Réactivité      | ✅ Neutre  | `subprocess.Popen` non bloquant — retour immédiat, osascript tourne en arrière-plan |

## 6. Notes

- `osascript` toujours disponible sur macOS, aucune installation requise
- `capture_output=True` évite que la sortie d'osascript pollue les logs SwiGi
- Alternative future : `UNUserNotificationCenter` via PyObjC pour icône custom — nécessiterait d'abord un build portable (.app)
