import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

from swigi.constants import DEVICE_TYPE_KEYBOARD, DEVICE_TYPE_MOUSE
from swigi.daemon import run_daemon
from swigi.discovery import find_all_devices
from swigi.gui import HAS_RUMPS, SwiGiMenuBar, notify

log = logging.getLogger("swigi.main")

_LOCK_FILE = os.path.expanduser("~/.swigi.lock")


def _acquire_lock() -> bool:
    """Vérifie qu'une seule instance tourne. Retourne False si déjà lancé.

    O_CREAT|O_EXCL = création atomique — élimine la race condition TOCTOU.
    Si le fichier existe déjà, vérifie si le PID est vivant avant de conclure.
    """
    try:
        lock_file_descriptor = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(lock_file_descriptor, "w") as lock_file:
            lock_file.write(str(os.getpid()))
        return True
    except FileExistsError:
        pass  # fichier existe — vérifier si le PID est vivant

    try:
        with open(_LOCK_FILE) as lock_file:
            process_id = int(lock_file.read().strip())
        os.kill(process_id, 0)
        return False  # instance vivante
    except (ValueError, OSError):
        # PID mort ou fichier corrompu — écraser le lock
        try:
            os.remove(_LOCK_FILE)
        except OSError:
            pass
        return _acquire_lock()  # relancer (max 1 récursion)


def _release_lock() -> None:
    try:
        os.remove(_LOCK_FILE)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SwiGi — synchronisation Easy-Switch via Bluetooth"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Journalisation détaillée"
    )
    parser.add_argument(
        "--log-file",
        metavar="FICHIER",
        help="Écrire les logs dans ce fichier (rotation auto : 1 Mo × 3)",
    )
    arguments = parser.parse_args()

    if not _acquire_lock():
        print("SwiGi est déjà en cours d'exécution.", file=sys.stderr)
        return 0

    try:
        return _main_inner(arguments)
    finally:
        _release_lock()


def _main_inner(arguments) -> int:
    level = logging.DEBUG if arguments.verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", datefmt="%H:%M:%S"
    )

    # Configuration propre du logger "swigi"
    swigi_logger = logging.getLogger("swigi")
    swigi_logger.setLevel(level)
    swigi_logger.propagate = False

    # Effacer les handlers existants s'il y en a (sécurité import multiple)
    swigi_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    swigi_logger.addHandler(console_handler)

    if arguments.log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            arguments.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        swigi_logger.addHandler(file_handler)

    log.info("SwiGi — recherche des périphériques...")

    keyboards = find_all_devices(DEVICE_TYPE_KEYBOARD)
    if not keyboards:
        log.error("Clavier introuvable ! Vérifie la connexion Bluetooth.")
        return 1
    for keyboard in keyboards:
        log.info(
            "Clavier : %s (Product ID=0x%04X, CHANGE_HOST index=%d)",
            keyboard.name,
            keyboard.product_id,
            keyboard.change_host_index,
        )
        notify(f"{keyboard.name} connecté", "Clavier")

    mice = find_all_devices(DEVICE_TYPE_MOUSE)
    if not mice:
        log.warning(
            "Souris introuvable au démarrage — probe loop cherchera automatiquement."
            " (Normal si la souris est connectée à un autre Mac.)"
        )
    for mouse in mice:
        log.info(
            "Souris :  %s (Product ID=0x%04X, CHANGE_HOST index=%d)",
            mouse.name,
            mouse.product_id,
            mouse.change_host_index,
        )
        notify(f"{mouse.name} connectée", "Souris")

    log.info("")
    keyboard_names = ", ".join(keyboard.name for keyboard in keyboards)
    log.info("Prêt. Appuie sur Easy-Switch sur %s.", keyboard_names)
    if not HAS_RUMPS:
        log.info("Ctrl+C pour quitter.")

    state: dict = {
        "keyboard": keyboards[0].name,
        "keyboards": {
            keyboard.product_id: {"name": keyboard.name, "ok": True}
            for keyboard in keyboards
        },
        "mouse": mice[0].name if mice else None,
        "mice": [mouse.name for mouse in mice],
        "switches": 0,
        "pending_host": None,
    }
    stop_event = threading.Event()

    def _on_stop(signal_number, stack_frame):
        stop_event.set()
        if HAS_RUMPS and SwiGiMenuBar:
            try:
                import rumps

                rumps.quit_application()
            except Exception:
                pass

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    def _daemon_loop(keyboards, mice, state, stop_event):
        while not stop_event.is_set():
            try:
                run_daemon(keyboards, mice, state, stop_event)
            except Exception:
                log.exception("Crash inattendu — redémarrage dans 5s...")
                notify("SwiGi a crashé — redémarrage...", "Erreur")
                if stop_event.is_set():
                    break
                time.sleep(5)
                new_keyboards = find_all_devices(DEVICE_TYPE_KEYBOARD)
                if new_keyboards:
                    for old in keyboards:
                        old.close()
                    keyboards = new_keyboards
                    state["keyboards"] = {
                        keyboard.product_id: {"name": keyboard.name, "ok": True}
                        for keyboard in keyboards
                    }
                    state["keyboard"] = keyboards[0].name if keyboards else None
                new_mice = find_all_devices(DEVICE_TYPE_MOUSE)
                if new_mice:
                    for old in mice:
                        old.close()
                    mice = new_mice
                    state["mice"] = [mouse.name for mouse in mice]
                    state["mouse"] = mice[0].name

    if HAS_RUMPS and SwiGiMenuBar:
        # Daemon en thread background, menu bar sur thread principal (requis AppKit)
        daemon_thread = threading.Thread(
            target=_daemon_loop, args=(keyboards, mice, state, stop_event), daemon=True
        )
        daemon_thread.start()
        SwiGiMenuBar(state, stop_event).run()
        stop_event.set()
        daemon_thread.join(timeout=3)
    else:
        _daemon_loop(keyboards, mice, state, stop_event)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
