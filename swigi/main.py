import argparse
import logging
import logging.handlers
import signal
import sys
import threading

from swigi.constants import DEVICE_TYPE_KEYBOARD, DEVICE_TYPE_MOUSE
from swigi.daemon import run_daemon
from swigi.discovery import find_device
from swigi.gui import HAS_RUMPS, SwiGiMenuBar, notify

log = logging.getLogger("swigi.main")


def main() -> int:
    parser = argparse.ArgumentParser(description="SwiGi — synchronisation Easy-Switch via Bluetooth")
    parser.add_argument("-v", "--verbose", action="store_true", help="Journalisation détaillée")
    parser.add_argument(
        "--log-file",
        metavar="FICHIER",
        help="Écrire les logs dans ce fichier (rotation auto : 1 Mo × 3)",
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    # Configuration propre du logger "swigi"
    swigi_logger = logging.getLogger("swigi")
    swigi_logger.setLevel(level)
    swigi_logger.propagate = False

    # Effacer les handlers existants s'il y en a (sécurité import multiple)
    swigi_logger.handlers.clear()

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    swigi_logger.addHandler(ch)

    if args.log_file:
        fh = logging.handlers.RotatingFileHandler(
            args.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        swigi_logger.addHandler(fh)

    log.info("SwiGi — recherche des périphériques...")

    kb = find_device(DEVICE_TYPE_KEYBOARD)
    if kb is None:
        log.error("Clavier introuvable ! Vérifie la connexion Bluetooth.")
        return 1
    log.info("Clavier : %s (CHANGE_HOST idx=%d)", kb.name, kb.change_host_idx)
    notify(f"{kb.name} connecté", "Clavier")

    mouse = find_device(DEVICE_TYPE_MOUSE)
    if mouse is None:
        log.error("Souris introuvable ! Vérifie la connexion Bluetooth.")
        kb.close()
        return 1
    log.info("Souris :  %s (CHANGE_HOST idx=%d)", mouse.name, mouse.change_host_idx)
    notify(f"{mouse.name} connectée", "Souris")

    log.info("")
    log.info("Prêt. Appuie sur Easy-Switch sur %s.", kb.name)
    if not HAS_RUMPS:
        log.info("Ctrl+C pour quitter.")

    state: dict = {"kb": kb.name, "mouse": mouse.name, "switches": 0}
    stop_event = threading.Event()

    def _on_stop(sig, frame):
        stop_event.set()
        if HAS_RUMPS and SwiGiMenuBar:
            try:
                import rumps

                rumps.quit_application()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    if HAS_RUMPS and SwiGiMenuBar:
        # Daemon en thread background, menu bar sur thread principal (requis AppKit)
        t = threading.Thread(target=run_daemon, args=(kb, mouse, state, stop_event), daemon=True)
        t.start()
        SwiGiMenuBar(state, stop_event).run()
        stop_event.set()
        t.join(timeout=3)
    else:
        run_daemon(kb, mouse, state, stop_event)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
