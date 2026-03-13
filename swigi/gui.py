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
        with open(PREFS_FILE) as prefs_file:
            data = json.load(prefs_file)
            data.setdefault("notifications", True)
            data.setdefault("mouse_follow", True)
            return data
    except Exception:
        return {"notifications": True, "mouse_follow": True}


def save_prefs(prefs: dict) -> None:
    try:
        with open(PREFS_FILE, "w") as prefs_file:
            json.dump(prefs, prefs_file)
    except Exception as error:
        log.warning("Impossible de sauvegarder les préférences : %s", error)


prefs = load_prefs()
_prefs_lock = threading.Lock()


def notify(message: str, subtitle: str = "") -> None:
    """Notification macOS via osascript. No-op si désactivé ou hors Darwin."""
    with _prefs_lock:
        notifications_enabled = prefs.get("notifications", True)
    if SYSTEM != "Darwin" or not notifications_enabled:
        return

    def _escape_string(text: str) -> str:
        text = text.replace("\\", "\\\\").replace('"', '\\"')
        return "".join(char for char in text if char >= " " or char in "\t")

    script = f'display notification "{_escape_string(message)}" with title "SwiGi"'
    if subtitle:
        script += f' subtitle "{_escape_string(subtitle)}"'
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


if HAS_RUMPS and _rumps:

    class SwiGiMenuBar(_rumps.App):
        def __init__(self, state: dict, stop_event: threading.Event):
            initial_keyboards = state.get("keyboards") or {}
            initial_keyboard_names = (
                [
                    keyboard_data["name"]
                    for keyboard_data in initial_keyboards.values()
                    if keyboard_data.get("ok") and keyboard_data.get("name")
                ]
                if initial_keyboards
                else []
            )
            initial_keyboard = (
                ", ".join(initial_keyboard_names)
                if initial_keyboard_names
                else state.get("keyboard")
            )
            initial_mice = state.get("mice") or []
            initial_mouse = (
                ", ".join(initial_mice) if initial_mice else state.get("mouse")
            )
            super().__init__("⌨️", quit_button=None)
            try:
                from AppKit import NSApplication

                NSApplication.sharedApplication().setActivationPolicy_(1)
            except Exception:
                pass
            self._state = state
            self._stop_event = stop_event

            self._keyboard_item = _rumps.MenuItem(
                f"Clavier : {initial_keyboard or '—'} {'✅' if initial_keyboard else '❌'}"
            )
            self._mouse_item = _rumps.MenuItem(
                f"Souris : {initial_mouse or '—'} {'✅' if initial_mouse else '❌'}"
            )
            self._count_item = _rumps.MenuItem("Basculements : 0")
            self._notify_item = _rumps.MenuItem(
                "Notifications", callback=self._toggle_notify
            )
            with _prefs_lock:
                self._notify_item.state = prefs.get("notifications", True)
                self._mouse_follow_item_state = prefs.get("mouse_follow", True)
            self._mouse_follow_item = _rumps.MenuItem(
                "Souris suit le clavier", callback=self._toggle_mouse_follow
            )
            self._mouse_follow_item.state = self._mouse_follow_item_state

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
                self._better_mouse_auto_item = _rumps.MenuItem(
                    "Appliquer profil BetterMouse auto",
                    callback=self._better_mouse_toggle_auto,
                )
                self._better_mouse_auto_item.state = bool(
                    prefs.get("better_mouse_auto_apply", False)
                )
                self._better_mouse_profile_menu = _rumps.MenuItem(
                    "Profil BetterMouse à appliquer"
                )
                self._rebuild_profile_menu()
                menu_items += [
                    None,
                    _rumps.MenuItem(
                        "Exporter config BetterMouse",
                        callback=self._better_mouse_export,
                    ),
                    self._better_mouse_profile_menu,
                    self._better_mouse_auto_item,
                ]
            else:
                self._better_mouse_auto_item = None
                self._better_mouse_profile_menu = None

            menu_items += [
                None,
                _rumps.MenuItem("Quitter", callback=self._quit),
            ]
            self.menu = menu_items

        # ── Refresh timer ──────────────────────────────────────────────────

        @_rumps.timer(2)
        def _refresh(self, _):
            # Support multi-clavier : construire le nom depuis state["keyboards"] si disponible
            # Lire sous lock pour éviter une iteration concurrente avec le thread daemon
            state_lock = self._state.get("_lock")
            if state_lock:
                with state_lock:
                    keyboards_copy = dict(self._state.get("keyboards") or {})
                    mice_copy = list(self._state.get("mice") or [])
            else:
                keyboards_copy = dict(self._state.get("keyboards") or {})
                mice_copy = list(self._state.get("mice") or [])

            if keyboards_copy:
                actifs = [
                    keyboard_data["name"]
                    for keyboard_data in keyboards_copy.values()
                    if keyboard_data.get("ok") and keyboard_data.get("name")
                ]
                keyboard = ", ".join(actifs) if actifs else None
                # Garder state["keyboard"] cohérent pour le reste du code
                self._state["keyboard"] = actifs[0] if actifs else None
            else:
                keyboard = self._state.get("keyboard")

            mouse_display = (
                ", ".join(mice_copy) if mice_copy else self._state.get("mouse")
            )
            switches = self._state.get("switches", 0)
            self._keyboard_item.title = (
                f"Clavier : {keyboard or '—'} {'✅' if keyboard else '❌'}"
            )
            self._mouse_item.title = (
                f"Souris : {mouse_display or '—'} {'✅' if mouse_display else '❌'}"
            )
            self._count_item.title = f"Basculements : {switches}"
            # Titre statique — l'état détaillé est dans les items du menu
            self.title = "⌨️"

        # ── Notifications ──────────────────────────────────────────────────

        def _toggle_notify(self, sender):
            enabled = not bool(sender.state)
            with _prefs_lock:
                prefs["notifications"] = enabled
                save_prefs(dict(prefs))
            sender.state = enabled

        # ── Souris suit le clavier ─────────────────────────────────────────

        def _toggle_mouse_follow(self, sender):
            enabled = not bool(sender.state)
            with _prefs_lock:
                prefs["mouse_follow"] = enabled
                save_prefs(dict(prefs))
            sender.state = enabled
            log.info("Souris suit le clavier : %s", "ON" if enabled else "OFF")

        # ── BetterMouse — export ───────────────────────────────────────────

        def _better_mouse_export(self, _):
            from swigi.bettermouse import export_current

            mouse_name = self._state.get("mouse") or "souris"
            default_name = mouse_name.replace(" ", "-").lower()
            try:
                path = export_current(name=default_name)
                file_name = os.path.basename(path)[:-5]  # sans .json
                notify(f"Profil exporté : {file_name}", "BetterMouse")
                log.info("BetterMouse : profil exporté → %s", path)
                self._rebuild_profile_menu()
            except Exception as error:
                log.warning("Export BetterMouse échoué : %s", error)
                notify(f"Export échoué : {error}", "Erreur")

        # ── BetterMouse — sélection profil ────────────────────────────────

        def _rebuild_profile_menu(self):
            """Reconstruit le sous-menu 'Profil BetterMouse à appliquer' depuis PROFILES_DIR."""
            if self._better_mouse_profile_menu is None:
                return
            from swigi.bettermouse import list_profiles

            # Vider le sous-menu existant
            for key in list(self._better_mouse_profile_menu.keys()):
                del self._better_mouse_profile_menu[key]

            with _prefs_lock:
                active = prefs.get("better_mouse_profile")

            # Entrée "aucun"
            none_item = _rumps.MenuItem(
                "(aucun)", callback=self._better_mouse_select_profile
            )
            none_item.state = active is None
            self._better_mouse_profile_menu["(aucun)"] = none_item

            profiles = list_profiles()
            if profiles:
                self._better_mouse_profile_menu[None] = _rumps.separator  # séparateur
                for name in profiles:
                    item = _rumps.MenuItem(
                        name, callback=self._better_mouse_select_profile
                    )
                    item.state = name == active
                    self._better_mouse_profile_menu[name] = item

        def _better_mouse_select_profile(self, sender):
            name = sender.title if sender.title != "(aucun)" else None
            with _prefs_lock:
                prefs["better_mouse_profile"] = name
                save_prefs(dict(prefs))
            # Mettre à jour les checkmarks
            for key, item in list(self._better_mouse_profile_menu.items()):
                if key is None:
                    continue
                item.state = item.title == (name or "(aucun)")
            log.info("BetterMouse : profil sélectionné → %s", name or "(aucun)")

        # ── BetterMouse — toggle auto-apply ──────────────────────────────

        def _better_mouse_toggle_auto(self, sender):
            enabled = not bool(sender.state)
            with _prefs_lock:
                prefs["better_mouse_auto_apply"] = enabled
                save_prefs(dict(prefs))
            sender.state = enabled
            log.info("BetterMouse auto-apply : %s", "ON" if enabled else "OFF")

        # ── Divers ────────────────────────────────────────────────────────

        def _hide_icon(self, _):
            notify(
                "Icône masquée — relancez SwiGi depuis le terminal ou via launchd pour réafficher"
            )
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
