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
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        pass  # fichier existe — vérifier si le PID est vivant

    try:
        with open(_LOCK_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
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
    parser = argparse.ArgumentParser(description="SwiGi — synchronisation Easy-Switch via Bluetooth")
    parser.add_argument("-v", "--verbose", action="store_true", help="Journalisation détaillée")
    parser.add_argument(
        "--log-file",
        metavar="FICHIER",
        help="Écrire les logs dans ce fichier (rotation auto : 1 Mo × 3)",
    )
    args = parser.parse_args()

    if not _acquire_lock():
        print("SwiGi est déjà en cours d'exécution.", file=sys.stderr)
        return 0

    try:
        return _main_inner(args)
    finally:
        _release_lock()


def _main_inner(args) -> int:
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

    keyboards = find_all_devices(DEVICE_TYPE_KEYBOARD)
    if not keyboards:
        log.error("Clavier introuvable ! Vérifie la connexion Bluetooth.")
        return 1
    for kb in keyboards:
        log.info("Clavier : %s (PID=0x%04X, CHANGE_HOST idx=%d)", kb.name, kb.pid, kb.change_host_idx)
        notify(f"{kb.name} connecté", "Clavier")

    mice = find_all_devices(DEVICE_TYPE_MOUSE)
    if not mice:
        log.error("Souris introuvable ! Vérifie la connexion Bluetooth.")
        for kb in keyboards:
            kb.close()
        return 1
    for mouse in mice:
        log.info("Souris :  %s (PID=0x%04X, CHANGE_HOST idx=%d)", mouse.name, mouse.pid, mouse.change_host_idx)
        notify(f"{mouse.name} connectée", "Souris")

    log.info("")
    kb_names = ", ".join(kb.name for kb in keyboards)
    log.info("Prêt. Appuie sur Easy-Switch sur %s.", kb_names)
    if not HAS_RUMPS:
        log.info("Ctrl+C pour quitter.")

    state: dict = {
        "kb": keyboards[0].name,
        "kbs": {kb.pid: {"name": kb.name, "ok": True} for kb in keyboards},
        "mouse": mice[0].name,
        "mice": [m.name for m in mice],
        "switches": 0,
        "pending_host": None,
    }
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
                keyboards_new = find_all_devices(DEVICE_TYPE_KEYBOARD)
                if keyboards_new:
                    keyboards = keyboards_new
                    state["kbs"] = {kb.pid: {"name": kb.name, "ok": True} for kb in keyboards}
                    state["kb"] = keyboards[0].name
                mice_new = find_all_devices(DEVICE_TYPE_MOUSE)
                if mice_new:
                    mice = mice_new
                    state["mice"] = [m.name for m in mice]
                    state["mouse"] = mice[0].name

    if HAS_RUMPS and SwiGiMenuBar:
        # Daemon en thread background, menu bar sur thread principal (requis AppKit)
        t = threading.Thread(
            target=_daemon_loop, args=(keyboards, mice, state, stop_event), daemon=True
        )
        t.start()
        SwiGiMenuBar(state, stop_event).run()
        stop_event.set()
        t.join(timeout=3)
    else:
        _daemon_loop(keyboards, mice, state, stop_event)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
