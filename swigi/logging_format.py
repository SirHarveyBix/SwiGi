"""Formateur de logs coloré pour le terminal.

Ajoute des couleurs ANSI et des emoji aux messages de log dans le terminal.
Les couleurs sont désactivées automatiquement si stdout n'est pas un TTY
(ex: redirection vers fichier, launchd, pipe).
"""

import logging
import sys

# ── Codes couleur ANSI ────────────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_COLORS = {
    "DEBUG": "\033[36m",       # cyan
    "INFO": "\033[32m",        # vert
    "WARNING": "\033[33m",     # jaune
    "ERROR": "\033[31m",       # rouge
    "CRITICAL": "\033[41;37m", # fond rouge, texte blanc
}

_ICONS = {
    "DEBUG": "·",
    "INFO": "→",
    "WARNING": "⚠",
    "ERROR": "✖",
    "CRITICAL": "💀",
}

# ── Patterns de messages avec emoji contextuels ───────────────────────────────

_MESSAGE_ICONS = {
    "★": "★",             # switch détecté (déjà dans le code)
    "Sync confirmée": "✓",
    "confirmée sur hôte": "✓",
    "CHANGE_HOST →": "⚡",
    "CHANGE_HOST envoi": "⚡",
    "Clavier": "⌨️",
    "Souris": "🖱️",
    "Reconnexion OK": "🔄",
    "reconnecté": "🔄",
    "Déconnecté": "🔌",
    "déconnecté": "🔌",
    "Watchdog": "👁️",
    "Désync": "🔀",
    "BetterMouse": "🐭",
    "Prêt": "🟢",
    "démarré": "🟢",
    "arrêté": "🔴",
}


class ColoredFormatter(logging.Formatter):
    """Formateur qui ajoute des couleurs ANSI au terminal.

    Désactivé si la sortie n'est pas un TTY (pipe, fichier, launchd).
    """

    def __init__(self, datefmt: str = "%H:%M:%S"):
        super().__init__(datefmt=datefmt)
        self._use_colors = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        level_name = record.levelname
        message = record.getMessage()

        if not self._use_colors:
            # Mode sans couleur : format simple avec icône
            icon = _ICONS.get(level_name, "·")
            timestamp = self.formatTime(record, self.datefmt)
            return f"{timestamp} {icon} {message}"

        # Mode coloré
        color = _COLORS.get(level_name, "")
        icon = _ICONS.get(level_name, "·")
        timestamp = f"{_DIM}{self.formatTime(record, self.datefmt)}{_RESET}"
        level_display = f"{color}{_BOLD}{icon}{_RESET}"

        # Colorer le message selon le niveau
        if level_name in ("WARNING", "ERROR", "CRITICAL"):
            formatted_message = f"{color}{message}{_RESET}"
        elif level_name == "DEBUG":
            formatted_message = f"{_DIM}{message}{_RESET}"
        else:
            formatted_message = message

        return f"{timestamp} {level_display} {formatted_message}"


class PlainFormatter(logging.Formatter):
    """Formateur sans couleur pour les fichiers de log."""

    def __init__(self, datefmt: str = "%H:%M:%S"):
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt=datefmt)
