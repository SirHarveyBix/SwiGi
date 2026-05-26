import json
import logging
import os
import subprocess
import threading
from swigi.constants import PREFS_FILE, SYSTEM

log = logging.getLogger("swigi.gui")

# rumps : icône menu bar macOS (optionnel)
try:
    import rumps as _rumps

    HAS_RUMPS = SYSTEM == "Darwin"
except ImportError:
    _rumps = None
    HAS_RUMPS = False


def load_prefs() -> dict:
    try:
        with open(PREFS_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("notifications", True)
            data.setdefault("mouse_follow", True)
            return data
    except Exception:
        return {"notifications": True, "mouse_follow": True}


def save_prefs(prefs: dict) -> None:
    try:
        with open(PREFS_FILE, "w") as f:
            json.dump(prefs, f)
    except Exception as e:
        log.warning("Impossible de sauvegarder les préférences : %s", e)


prefs = load_prefs()
_prefs_lock = threading.Lock()


def notify(message: str, subtitle: str = "") -> None:
    """Notification macOS via osascript. No-op si désactivé ou hors Darwin."""
    with _prefs_lock:
        notifications_enabled = prefs.get("notifications", True)
    if SYSTEM != "Darwin" or not notifications_enabled:
        return

    def _esc(s: str) -> str:
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return ''.join(c for c in s if c >= ' ' or c in '\t')

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


if HAS_RUMPS and _rumps:

    class SwiGiMenuBar(_rumps.App):
        def __init__(self, state: dict, stop_event: threading.Event):
            keyboard0 = state.get("keyboard")
            mouse0 = state.get("mouse")
            super().__init__("⌨️" if (keyboard0 and mouse0) else "⌨", quit_button=None)
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().setActivationPolicy_(1)
            except Exception:
                pass
            self._state = state
            self._stop_event = stop_event

            self._keyboard_item    = _rumps.MenuItem(f"Clavier : {keyboard0 or '—'} {'✅' if keyboard0 else '❌'}")
            self._mouse_item = _rumps.MenuItem(f"Souris : {mouse0 or '—'} {'✅' if mouse0 else '❌'}")
            self._count_item = _rumps.MenuItem("Basculements : 0")
            self._notify_item = _rumps.MenuItem("Notifications", callback=self._toggle_notify)
            self._notify_item.state = prefs.get("notifications", True)
            self._mouse_follow_item = _rumps.MenuItem(
                "Souris suit le clavier", callback=self._toggle_mouse_follow
            )
            self._mouse_follow_item.state = prefs.get("mouse_follow", True)

            menu_items = [
                self._keyboard_item,
                self._mouse_item,
                None,
                self._count_item,
                None,
                self._mouse_follow_item,
                self._notify_item,
                _rumps.MenuItem("Masquer l'icône", callback=self._hide_icon),
            ]

            # Section BetterMouse — uniquement si installé
            from swigi.bettermouse import is_available
            if is_available():
                self._bm_auto_item = _rumps.MenuItem(
                    "Appliquer profil BetterMouse auto",
                    callback=self._bm_toggle_auto,
                )
                self._bm_auto_item.state = bool(prefs.get("bm_auto_apply", False))
                self._bm_profile_menu = _rumps.MenuItem("Profil BetterMouse à appliquer")
                self._rebuild_profile_menu()
                menu_items += [
                    None,
                    _rumps.MenuItem("Exporter config BetterMouse", callback=self._bm_export),
                    self._bm_profile_menu,
                    self._bm_auto_item,
                ]
            else:
                self._bm_auto_item = None
                self._bm_profile_menu = None

            menu_items += [
                None,
                _rumps.MenuItem("Quitter", callback=self._quit),
            ]
            self.menu = menu_items

        # ── Refresh timer ──────────────────────────────────────────────────

        @_rumps.timer(2)
        def _refresh(self, _):
            # Support multi-clavier : construire le nom depuis state["kbs"] si disponible
            kbs = self._state.get("kbs")
            if kbs:
                actifs = [d["name"] for d in kbs.values() if d.get("ok") and d.get("name")]
                kb = ", ".join(actifs) if actifs else None
                # Garder state["kb"] cohérent pour le reste du code
                self._state["kb"] = actifs[0] if actifs else None
            else:
                kb = self._state.get("kb")
            mouse = self._state.get("mouse")
            switches = self._state.get("switches", 0)
            self._kb_item.title    = f"Clavier : {kb or '—'} {'✅' if kb else '❌'}"
            self._mouse_item.title = f"Souris : {mouse or '—'} {'✅' if mouse else '❌'}"
            self._count_item.title = f"Basculements : {switches}"
            self.title = "⌨️" if (kb and mouse) else "⌨"

        # ── Notifications ──────────────────────────────────────────────────

        def _toggle_notify(self, sender):
            enabled = not bool(sender.state)
            with _prefs_lock:
                prefs["notifications"] = enabled
            save_prefs(prefs)
            sender.state = enabled

        # ── Souris suit le clavier ─────────────────────────────────────────

        def _toggle_mouse_follow(self, sender):
            enabled = not bool(sender.state)
            with _prefs_lock:
                prefs["mouse_follow"] = enabled
            save_prefs(prefs)
            sender.state = enabled
            log.info("Souris suit le clavier : %s", "ON" if enabled else "OFF")

        # ── BetterMouse — export ───────────────────────────────────────────

        def _bm_export(self, _):
            from swigi.bettermouse import export_current
            mouse_name = self._state.get("mouse") or "souris"
            default_name = mouse_name.replace(" ", "-").lower()
            try:
                path = export_current(name=default_name)
                fname = os.path.basename(path)[:-5]  # sans .json
                notify(f"Profil exporté : {fname}", "BetterMouse")
                log.info("BetterMouse : profil exporté → %s", path)
                self._rebuild_profile_menu()
            except Exception as e:
                log.warning("Export BetterMouse échoué : %s", e)
                notify(f"Export échoué : {e}", "Erreur")

        # ── BetterMouse — sélection profil ────────────────────────────────

        def _rebuild_profile_menu(self):
            """Reconstruit le sous-menu 'Profil BetterMouse à appliquer' depuis PROFILES_DIR."""
            if self._bm_profile_menu is None:
                return
            from swigi.bettermouse import list_profiles

            # Vider le sous-menu existant
            for key in list(self._bm_profile_menu.keys()):
                del self._bm_profile_menu[key]

            active = prefs.get("bm_profile")

            # Entrée "aucun"
            none_item = _rumps.MenuItem("(aucun)", callback=self._bm_select_profile)
            none_item.state = (active is None)
            self._bm_profile_menu["(aucun)"] = none_item

            profiles = list_profiles()
            if profiles:
                self._bm_profile_menu[None] = _rumps.separator  # séparateur
                for name in profiles:
                    item = _rumps.MenuItem(name, callback=self._bm_select_profile)
                    item.state = (name == active)
                    self._bm_profile_menu[name] = item

        def _bm_select_profile(self, sender):
            name = sender.title if sender.title != "(aucun)" else None
            with _prefs_lock:
                prefs["bm_profile"] = name
            save_prefs(prefs)
            # Mettre à jour les checkmarks
            for key, item in self._bm_profile_menu.items():
                if key is None:
                    continue
                item.state = (item.title == (name or "(aucun)"))
            log.info("BetterMouse : profil sélectionné → %s", name or "(aucun)")

        # ── BetterMouse — toggle auto-apply ──────────────────────────────

        def _bm_toggle_auto(self, sender):
            enabled = not bool(sender.state)
            with _prefs_lock:
                prefs["bm_auto_apply"] = enabled
            save_prefs(prefs)
            sender.state = enabled
            log.info("BetterMouse auto-apply : %s", "ON" if enabled else "OFF")

        # ── Divers ────────────────────────────────────────────────────────

        def _hide_icon(self, _):
            notify("Icône masquée — relance SwiGi pour réafficher")
            try:
                # rumps expose NSStatusItem via _status_item depuis rumps >= 0.4
                item = getattr(self, "_status_item", None)
                if item is not None:
                    item.setVisible_(False)
                else:
                    self.title = ""
            except Exception:
                pass

        def _quit(self, _):
            self._stop_event.set()
            _rumps.quit_application()

else:
    SwiGiMenuBar = None
