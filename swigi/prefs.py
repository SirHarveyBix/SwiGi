"""Gestion des préférences utilisateur SwiGi.

Module indépendant (pas de dépendance sur gui) — chargeable sur toutes plateformes.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

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
        prefs_dir = Path(PREFS_FILE).parent
        with tempfile.NamedTemporaryFile(
            "w", dir=prefs_dir, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            json.dump(data, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, PREFS_FILE)
    except Exception as error:
        log.warning("Impossible de sauvegarder les préférences : %s", error)


prefs = load_prefs()
