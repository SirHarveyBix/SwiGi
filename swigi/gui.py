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
            return json.load(f)
    except Exception:
        return {"notifications": True}


def save_prefs(prefs: dict) -> None:
    try:
        with open(PREFS_FILE, "w") as f:
            json.dump(prefs, f)
    except Exception as e:
        log.warning("Impossible de sauvegarder les préférences : %s", e)


prefs = load_prefs()


def notify(message: str, subtitle: str = "") -> None:
    """Notification macOS via osascript. No-op si désactivé ou hors Darwin."""
    if SYSTEM != "Darwin" or not prefs.get("notifications", True):
        return

    def _esc(s: str) -> str:
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return ''.join(c for c in s if c >= ' ' or c in '\t')  # retire \n, \r, etc.

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
            kb0 = state.get("kb")
            mouse0 = state.get("mouse")
            super().__init__("⌨️" if (kb0 and mouse0) else "⌨", quit_button=None)
            try:
                from AppKit import NSApplication
                NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory — no Dock icon
            except Exception:
                pass
            self._state = state
            self._stop_event = stop_event
            self._kb_item = _rumps.MenuItem(f"Clavier : {kb0 or '—'} {'✅' if kb0 else '❌'}")
            self._mouse_item = _rumps.MenuItem(f"Souris : {mouse0 or '—'} {'✅' if mouse0 else '❌'}")
            self._count_item = _rumps.MenuItem("Basculements : 0")
            self._notify_item = _rumps.MenuItem("Notifications", callback=self._toggle_notify)
            self._notify_item.state = prefs.get("notifications", True)
            self.menu = [
                self._kb_item,
                self._mouse_item,
                None,
                self._count_item,
                None,
                self._notify_item,
                _rumps.MenuItem("Masquer l'icône", callback=self._hide_icon),
                None,
                _rumps.MenuItem("Quitter", callback=self._quit),
            ]

        @_rumps.timer(2)
        def _refresh(self, _):
            kb = self._state.get("kb")
            mouse = self._state.get("mouse")
            switches = self._state.get("switches", 0)
            self._kb_item.title = f"Clavier : {kb or '—'} {'✅' if kb else '❌'}"
            self._mouse_item.title = f"Souris : {mouse or '—'} {'✅' if mouse else '❌'}"
            self._count_item.title = f"Basculements : {switches}"
            self.title = "⌨️" if (kb and mouse) else "⌨"

        def _toggle_notify(self, sender):
            enabled = not bool(sender.state)
            prefs["notifications"] = enabled
            save_prefs(prefs)
            sender.state = enabled

        def _hide_icon(self, _):
            notify("Icône masquée — relance SwiGi pour réafficher")
            try:
                self._status_item.setVisible_(False)
            except Exception:
                pass

        def _quit(self, _):
            self._stop_event.set()
            _rumps.quit_application()

else:
    SwiGiMenuBar = None
