# Spec : Icône menu bar macOS

**Version :** 1.1.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

Quand SwiGi tourne en arrière-plan (autostart launchd), l'utilisateur n'a aucun moyen de savoir si l'outil est actif et si ses périphériques sont connectés sans ouvrir un Terminal. L'icône menu bar donne un retour visuel permanent sans friction.

## 2. Périmètre

**Inclus :**

- Icône `⌨️` (colorée = connecté) ou `⌨` (monochrome = au moins un périphérique absent) dans la barre de menu
- Affichage nom clavier + statut ✅/❌
- Affichage nom souris + statut ✅/❌
- Compteur de basculements
- Toggle notifications système (persisté dans `~/.swigi_prefs.json`)
- Bouton "Masquer l'icône" (icône cachée jusqu'au prochain redémarrage)
- Bouton Quitter (arrêt propre daemon + menu bar)
- Mise à jour toutes les 2 secondes

**Exclus :**

- Windows / Linux (APIs différentes, hors périmètre)
- Icône custom (image PNG) — requiert bundle .app
- Actions dans le menu (configurer hôte, forcer switch, etc.) — v2

## 3. Exigences fonctionnelles

| #   | Exigence                                                                     | Priorité |
| --- | ---------------------------------------------------------------------------- | -------- |
| F1  | Icône visible en permanence dans la barre de menu                            | MUST     |
| F2  | Statut clavier et souris corrects dès l'ouverture du menu (sans attendre 2s) | MUST     |
| F3  | Icône `⌨️` → `⌨` (monochrome) quand un périphérique est absent               | MUST     |
| F4  | Statut mis à jour en temps quasi-réel (≤ 2s) via timer                       | MUST     |
| F5  | Quitter depuis le menu arrête proprement le daemon                           | MUST     |
| F6  | Sans rumps installé : comportement identique à avant (fallback silencieux)   | MUST     |
| F7  | install_mac.sh installe rumps automatiquement                                | MUST     |
| F8  | Toggle notifications persisté dans `~/.swigi_prefs.json`                     | SHOULD   |
| F9  | Masquer l'icône sans quitter le daemon                                       | SHOULD   |

## 4. Architecture

**Contrainte AppKit :** le runloop AppKit (et donc rumps) DOIT tourner sur le thread principal. Le daemon HID++ tourne en thread background (`threading.Thread`, daemon=True).

```
Thread principal : SwiGiMenuBar(rumps.App).run()  ← AppKit runloop
Thread background : _run_daemon(kb, mouse, state, stop_event)

Communication : dict `state` partagé (lecture/écriture atomique GIL Python)
  state["kb"]       → str | None   (nom clavier ou None si déconnecté)
  state["mouse"]    → str | None   (nom souris ou None si déconnectée)
  state["switches"] → int          (compteur basculements)

Arrêt : threading.Event stop_event
  SIGINT/SIGTERM → stop_event.set() + rumps.quit_application()
  Quitter menu   → stop_event.set() + rumps.quit_application()
```

**Invariant synchronisation :** `state` est initialisé avec les noms réels des périphériques dans `main()` avant le démarrage du thread daemon. Le menu bar affiche des données correctes dès la première ouverture, sans race condition.

**Règle déconnexion post-switch :** quand le daemon détecte une notification Easy-Switch, `state["kb"]` et `state["mouse"]` sont mis à `None` simultanément dans la même itération de boucle (lors de la déconnexion clavier post-switch). Le menu affiche les deux périphériques comme absents correctement.

## 5. Implémentation clé

```python
if _HAS_RUMPS:
    class SwiGiMenuBar(_rumps.App):
        def __init__(self, state, stop_event):
            kb0 = state.get("kb")
            mouse0 = state.get("mouse")
            # Icône et items initialisés avec l'état réel, pas de flash ❌
            super().__init__("⌨️" if (kb0 and mouse0) else "⌨", quit_button=None)
            self._kb_item = _rumps.MenuItem(
                f"Clavier : {kb0 or '—'} {'✅' if kb0 else '❌'}")
            self._mouse_item = _rumps.MenuItem(
                f"Souris : {mouse0 or '—'} {'✅' if mouse0 else '❌'}")
            ...

        @_rumps.timer(2)
        def _refresh(self, _):
            kb = self._state.get("kb")
            mouse = self._state.get("mouse")
            self._kb_item.title = f"Clavier : {kb or '—'} {'✅' if kb else '❌'}"
            self._mouse_item.title = f"Souris : {mouse or '—'} {'✅' if mouse else '❌'}"
            self.title = "⌨️" if (kb and mouse) else "⌨"
```

## 6. Conformité constitution

| Principe        | Impact       | Mesure                                                                            |
| --------------- | ------------ | --------------------------------------------------------------------------------- |
| Simplicité      | ⚠️ Exception | `rumps` installé par `install_mac.sh` — pas requis manuellement. P1 amendé v1.1.0 |
| Portabilité     | ✅ Neutre    | Guard `_HAS_RUMPS`, fallback silencieux sur Windows/Linux                         |
| Robustesse      | ✅ Positif   | Quitter depuis menu = arrêt propre (vs Ctrl+C)                                    |
| Non-intrusivité | ✅ Neutre    | Icône standard macOS, pas de fenêtre                                              |
| Réactivité      | ✅ Neutre    | Daemon en thread séparé, menu bar n'impacte pas la latence HID++                  |
