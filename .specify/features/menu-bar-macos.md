# Spec : Icône menu bar macOS

**Version :** 1.0.0
**Date :** 2026-05-22
**Statut :** Implémenté

---

## 1. Contexte

Quand SwiGi tourne en arrière-plan (autostart launchd), l'utilisateur n'a aucun moyen de savoir si l'outil est actif et si ses périphériques sont connectés sans ouvrir un Terminal. L'icône menu bar donne un retour visuel permanent sans friction.

## 2. Périmètre

**Inclus :**

- Icône `⌨️` persistante dans la barre de menu macOS
- Affichage nom clavier + statut ✅/❌
- Affichage nom souris + statut ✅/❌
- Compteur de basculements
- Bouton Quitter (arrêt propre daemon + menu bar)
- Mise à jour toutes les 2 secondes

**Exclus :**

- Windows / Linux (APIs différentes, hors périmètre)
- Icône custom (image PNG) — requiert bundle .app
- Actions dans le menu (configurer hôte, forcer switch, etc.) — v2

## 3. Exigences fonctionnelles

| #   | Exigence                                                                   | Priorité |
| --- | -------------------------------------------------------------------------- | -------- |
| F1  | Icône visible en permanence dans la barre de menu                          | MUST     |
| F2  | Statut clavier et souris mis à jour en temps quasi-réel (≤ 2s)             | MUST     |
| F3  | Quitter depuis le menu arrête proprement le daemon                         | MUST     |
| F4  | Sans rumps installé : comportement identique à avant (fallback silencieux) | MUST     |
| F5  | install_mac.sh installe rumps automatiquement                              | MUST     |

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

## 5. Implémentation clé

```python
try:
    import rumps as _rumps
    _HAS_RUMPS = _SYSTEM == "Darwin"
except ImportError:
    _rumps = None
    _HAS_RUMPS = False

if _HAS_RUMBS:
    class SwiGiMenuBar(_rumps.App):
        @_rumps.timer(2)
        def _refresh(self, _):
            kb = self._state.get("kb")
            mouse = self._state.get("mouse")
            self._kb_item.title = f"Clavier : {kb or '—'} {'✅' if kb else '❌'}"
            self._mouse_item.title = f"Souris : {mouse or '—'} {'✅' if mouse else '❌'}"
            self.title = "⌨️" if (kb and mouse) else "⌨️⚠"
```

## 6. Conformité constitution

| Principe        | Impact       | Mesure                                                                            |
| --------------- | ------------ | --------------------------------------------------------------------------------- |
| Simplicité      | ⚠️ Exception | `rumps` installé par `install_mac.sh` — pas requis manuellement. P1 amendé v1.1.0 |
| Portabilité     | ✅ Neutre    | Guard `_HAS_RUMPS`, fallback silencieux sur Windows/Linux                         |
| Robustesse      | ✅ Positif   | Quitter depuis menu = arrêt propre (vs Ctrl+C)                                    |
| Non-intrusivité | ✅ Neutre    | Icône standard macOS, pas de fenêtre                                              |
| Réactivité      | ✅ Neutre    | Daemon en thread séparé, menu bar n'impacte pas la latence HID++                  |
