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

# ── Couleurs par emoji de début de message (INFO level) ──────────────────────
# Donne une hiérarchie visuelle immédiate pour l'utilisateur.

_MSG_COLORS = (
    # Événement principal — switch Easy-Switch (le plus important)
    ("★",  "\033[1;92m"),   # bold bright green
    ("━",  "\033[1;92m"),   # bold bright green  — séparateur switch
    # Succès
    ("✓",  "\033[92m"),     # bright green
    # Commande envoyée à la souris
    ("⚡",  "\033[96m"),     # bright cyan
    ("⏩",  "\033[96m"),     # bright cyan       — envoi différé
    # Périphériques — clavier & souris
    ("⌨",  "\033[94m"),     # bright blue
    ("🖱",  "\033[94m"),     # bright blue
    # Reconnexion (informationnel)
    ("🔄",  "\033[94m"),     # bright blue
    # Déconnexion (bruit de fond — atténué)
    ("🔌",  "\033[90m"),     # dark grey
    # BetterMouse
    ("🐭",  "\033[95m"),     # magenta
    # Démarrage / arrêt
    ("🟢",  "\033[1;92m"),   # bold bright green
    ("🔴",  "\033[1;91m"),   # bold bright red
    # Watchdog (proche d'un warning)
    ("👁",  "\033[33m"),     # yellow
    # Debounce / skip (neutre)
    ("⏭",  "\033[90m"),     # dark grey
    ("⏳",  "\033[90m"),     # dark grey
    # Suivi désactivé
    ("🚫",  "\033[90m"),     # dark grey
)


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
            icon = _ICONS.get(level_name, "·")
            timestamp = self.formatTime(record, self.datefmt)
            return f"{timestamp} {icon} {message}"

        color = _COLORS.get(level_name, "")
        icon = _ICONS.get(level_name, "·")
        timestamp = f"{_DIM}{self.formatTime(record, self.datefmt)}{_RESET}"
        level_display = f"{color}{_BOLD}{icon}{_RESET}"

        if level_name in ("WARNING", "ERROR", "CRITICAL"):
            formatted_message = f"{color}{message}{_RESET}"
        elif level_name == "DEBUG":
            formatted_message = f"{_DIM}{message}{_RESET}"
        else:
            # INFO : couleur par emoji de début de message
            msg_color = next(
                (c for prefix, c in _MSG_COLORS if message.startswith(prefix)),
                None,
            )
            formatted_message = (
                f"{msg_color}{message}{_RESET}" if msg_color else message
            )

        return f"{timestamp} {level_display} {formatted_message}"


class PlainFormatter(logging.Formatter):
    """Formateur sans couleur pour les fichiers de log."""

    def __init__(self, datefmt: str = "%H:%M:%S"):
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt=datefmt)
