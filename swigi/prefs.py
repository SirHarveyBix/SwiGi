"""Gestion des préférences utilisateur SwiGi.

Module indépendant (pas de dépendance sur gui) — chargeable sur toutes plateformes.
"""

import json
import logging
import threading

from swigi.constants import PREFS_FILE

log = logging.getLogger("swigi.prefs")

prefs: dict = {}
_prefs_lock = threading.Lock()


def load_prefs() -> dict:
    try:
        with open(PREFS_FILE) as prefs_file:
            data = json.load(prefs_file)
            data.setdefault("notifications", True)
            data.setdefault("mouse_follow", True)
            return data
    except Exception:
        return {"notifications": True, "mouse_follow": True}


def save_prefs(data: dict) -> None:
    try:
        with open(PREFS_FILE, "w") as prefs_file:
            json.dump(data, prefs_file)
    except Exception as error:
        log.warning("Impossible de sauvegarder les préférences : %s", error)


prefs = load_prefs()
