import logging
import threading
import time

from swigi.constants import (
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    DEVNUMBER_DIRECT,
    MSG_LENGTHS,
    PING_MSG,
)
from swigi.discovery import DeviceInfo, find_device
from swigi.gui import notify
from swigi.protocol import _verify_and_sync, send_change_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.daemon")


def run_daemon(
    kb: DeviceInfo,
    mouse: DeviceInfo,
    state: dict,
    stop_event: threading.Event,
) -> None:
    state["kb"] = kb.name
    state["mouse"] = mouse.name

    total_switches = 0
    last_response = time.time()
    last_switch_time = 0.0  # timestamp de la dernière notification Easy-Switch détectée
    last_mouse_probe = 0.0  # timestamp de la dernière sonde de reconnexion souris
    WATCHDOG_TIMEOUT = 10.0

    while not stop_event.is_set():
        # ── Watchdog ──
        if time.time() - last_response > WATCHDOG_TIMEOUT:
            log.info("Watchdog : aucune réponse depuis %ds, reconnexion...", int(WATCHDOG_TIMEOUT))
            kb.close()
            mouse.close()
            state["kb"] = None
            state["mouse"] = None
            time.sleep(1.0)
            kb_new = find_device(DEVICE_TYPE_KEYBOARD)
            if kb_new:
                kb = kb_new
                state["kb"] = kb.name
                log.info("Watchdog reconnexion clavier : %s", kb.name)
            mouse_new = find_device(DEVICE_TYPE_MOUSE)
            if mouse_new:
                mouse = mouse_new
                state["mouse"] = mouse.name
                log.info("Watchdog reconnexion souris : %s", mouse.name)
            if state.get("kb") and state.get("mouse"):
                _verify_and_sync(kb, mouse, state)
            last_response = time.time()
            continue

        # ── Ping ──
        try:
            kb.transport.write(PING_MSG)
        except (TransportError, OSError):
            switch_triggered = time.time() - last_switch_time <= 3.0
            log.info("Clavier déconnecté%s", " (post-switch)" if switch_triggered else "")
            if not switch_triggered:
                notify(f"{kb.name} déconnecté", "Clavier")
            kb.close()
            state["kb"] = None
            if switch_triggered:
                # La souris a aussi basculé — marquer déconnectée immédiatement
                mouse.close()
                state["mouse"] = None

            kb_new = None
            for attempt in range(600):
                if stop_event.is_set():
                    break
                time.sleep(0.1)
                kb_new = find_device(DEVICE_TYPE_KEYBOARD)
                if kb_new is not None:
                    break
                if attempt % 100 == 99:
                    log.debug("Reconnexion : tentative %d/600...", attempt + 1)

            if kb_new is None:
                if not stop_event.is_set():
                    log.warning("Le clavier n'est pas revenu, nouvelle tentative...")
                continue
            kb = kb_new
            state["kb"] = kb.name
            log.info("Reconnexion clavier : %s", kb.name)
            notify(f"{kb.name} reconnecté", "Clavier")
            last_response = time.time()

            mouse.close()
            state["mouse"] = None
            log.debug("Reconnexion proactive de la souris...")
            new_mouse = find_device(DEVICE_TYPE_MOUSE)
            if new_mouse:
                mouse = new_mouse
                state["mouse"] = mouse.name
                log.debug("Souris prête : %s — vérification sync hôtes...", mouse.name)
                _verify_and_sync(kb, mouse, state)
                if state["mouse"]:  # non None = sync OK ou pas de désync
                    notify(f"{mouse.name} reconnectée", "Souris")
            else:
                log.debug("Souris introuvable, nouvelle tentative au prochain événement")
            continue

        # ── Lecture réponses (fenêtre 80ms) ──
        deadline = time.time() + 0.08
        while time.time() < deadline and not stop_event.is_set():
            try:
                raw = kb.transport.read(timeout=25)
            except (TransportError, OSError):
                break

            if raw is None or len(raw) < 4:
                continue
            rid = raw[0]
            if rid not in MSG_LENGTHS or len(raw) != MSG_LENGTHS[rid]:
                continue

            feat = raw[2]
            func = raw[3]
            sw_id = func & 0x0F
            last_response = time.time()

            # Notification CHANGE_HOST
            if feat == kb.change_host_idx and sw_id == 0 and len(raw) > 5:
                target_host = raw[5]
                last_switch_time = time.time()  # le clavier va se déconnecter quoi qu'il arrive
                log.info("")
                log.info("★ Easy-Switch : %s → hôte %d", kb.name, target_host)

                if not mouse.transport.is_open:
                    log.debug("Transport souris fermé, reconnexion...")
                    new_mouse = find_device(DEVICE_TYPE_MOUSE)
                    if new_mouse:
                        mouse = new_mouse
                        state["mouse"] = mouse.name
                    else:
                        log.info("Souris indisponible — basculera au prochain Easy-Switch")
                        break

                try:
                    send_change_host(
                        mouse.transport,
                        DEVNUMBER_DIRECT,
                        mouse.change_host_idx,
                        target_host,
                    )
                    log.info("★ CHANGE_HOST → %s → hôte %d", mouse.name, target_host)
                    total_switches += 1
                    state["switches"] = total_switches

                    # ── Vérification : la souris doit déconnecter dans 300ms ──
                    # Si elle répond encore au ping → elle n'a pas basculé → retry
                    _t_verify = time.time() + 0.3
                    while time.time() < _t_verify and not stop_event.is_set():
                        time.sleep(0.02)
                    mouse_confirmed = False
                    try:
                        mouse.transport.write(PING_MSG)
                        log.warning("Souris encore connectée après 300ms — retry switch...")
                    except (TransportError, OSError):
                        mouse_confirmed = True  # déconnectée = switch reçu

                    if not mouse_confirmed:
                        for _r in range(3):
                            try:
                                send_change_host(
                                    mouse.transport,
                                    DEVNUMBER_DIRECT,
                                    mouse.change_host_idx,
                                    target_host,
                                )
                            except (TransportError, OSError):
                                mouse_confirmed = True
                                break
                            _t_r = time.time() + 0.2
                            while time.time() < _t_r and not stop_event.is_set():
                                time.sleep(0.02)
                            try:
                                mouse.transport.write(PING_MSG)
                            except (TransportError, OSError):
                                mouse_confirmed = True
                                break
                        if mouse_confirmed:
                            log.info("Souris confirmée au retry")
                        else:
                            log.warning("Désync probable — sera corrigée au reconnect")
                            notify("Switch souris incertain — correction au reconnect", "SwiGi")

                    # Correction robuste : si bascule confirmée, fermer et réinitialiser
                    if mouse_confirmed:
                        log.info("Bascule de la souris confirmée. Fermeture du transport.")
                        mouse.close()
                        state["mouse"] = None

                except (TransportError, OSError):
                    log.warning("CHANGE_HOST souris échoué, reconnexion...")
                    mouse.close()
                    state["mouse"] = None
                    time.sleep(0.5)
                    new_mouse = find_device(DEVICE_TYPE_MOUSE)
                    if new_mouse:
                        mouse = new_mouse
                        state["mouse"] = mouse.name
                        try:
                            send_change_host(
                                mouse.transport,
                                DEVNUMBER_DIRECT,
                                mouse.change_host_idx,
                                target_host,
                            )
                            log.info(
                                "★ CHANGE_HOST → %s → hôte %d (après reconnexion)",
                                mouse.name,
                                target_host,
                            )
                            total_switches += 1
                            state["switches"] = total_switches
                        except (TransportError, OSError) as e:
                            log.warning("Retry CHANGE_HOST échoué : %s", e)
                    else:
                        log.info("Souris indisponible — basculera au prochain Easy-Switch")

                break  # le clavier va se déconnecter

            if sw_id == 0:
                log.debug("Notification : feat=0x%02X [%s]", feat, raw[:10].hex())

        time.sleep(0.01)

        # Sonde périodique : reconnecter la souris si absente et clavier OK
        if state.get("mouse") is None and time.time() - last_mouse_probe > 5.0:
            last_mouse_probe = time.time()
            new_mouse = find_device(DEVICE_TYPE_MOUSE)
            if new_mouse:
                if mouse.transport.is_open:
                    mouse.close()
                mouse = new_mouse
                state["mouse"] = mouse.name
                log.info(
                    "Souris reconnectée automatiquement : %s — vérification sync...",
                    mouse.name,
                )
                _verify_and_sync(kb, mouse, state)
                if state["mouse"]:
                    notify(f"{mouse.name} reconnectée", "Souris")

    log.info("Arrêt. Total : %d basculements.", total_switches)
    kb.close()
    mouse.close()
