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
from swigi.constants import SYSTEM
from swigi.discovery import DeviceInfo, find_device
from swigi.gui import notify, prefs
from swigi.protocol import get_current_host, send_change_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.daemon")

_PENDING_HOST_TTL = 60.0  # secondes avant abandon de la correction pending


def _apply_bm_profile_if_needed(mouse_name: str | None = None) -> None:
    """Applique le profil BetterMouse configuré si auto-apply est activé.

    No-op si BetterMouse absent, profil non configuré, ou toggle désactivé.
    Toujours silencieux en cas d'erreur (ne bloque jamais la boucle principale).
    """
    if SYSTEM != "Darwin":
        return
    if not prefs.get("bm_auto_apply") or not prefs.get("bm_profile"):
        return
    try:
        from swigi.bettermouse import apply_profile
        apply_profile(prefs["bm_profile"], mouse_name=mouse_name)
        notify(f"Profil {prefs['bm_profile']} appliqué", "BetterMouse")
        log.info("BetterMouse : profil '%s' appliqué", prefs["bm_profile"])
    except ValueError as e:
        log.warning("BetterMouse : profil ignoré (souris différente) : %s", e)
    except Exception as e:
        log.warning("BetterMouse : apply_profile échoué : %s", e)


def _resync_pending_host_from_keyboard(kb: DeviceInfo, state: dict) -> None:
    """Met à jour pending_host d'après l'hôte réel du clavier après reconnexion.

    Corrige le cas où un pending_host stale (du switch précédent) enverrait la
    souris sur le mauvais hôte lors du retour — surtout avec 3 hôtes ou 2 claviers.
    """
    kb_host = get_current_host(kb.transport, DEVNUMBER_DIRECT, kb.change_host_idx)
    if kb_host is not None:
        state["pending_host"] = (kb_host, time.time() + _PENDING_HOST_TTL)
        log.debug("pending_host recalé sur hôte clavier : %d", kb_host)
    else:
        state["pending_host"] = None
        log.debug("pending_host effacé (hôte clavier illisible)")


def _check_and_apply_pending_host(mouse: DeviceInfo, state: dict) -> bool:
    """Vérifie que la souris est sur le bon hôte après un switch.

    Compare l'hôte actuel de la souris avec state["pending_host"].
    Si désync → envoie CHANGE_HOST correctif, ferme le transport.
    Si sync OK → efface pending_host, laisse mouse ouvert.

    Retourne True si le transport souris a été fermé (correction ou lecture impossible).
    """
    pending = state.get("pending_host")
    if pending is None:
        return False
    target_host, deadline = pending
    if time.time() > deadline:
        log.debug("pending_host expiré — abandon")
        state["pending_host"] = None
        return False

    current = get_current_host(mouse.transport, DEVNUMBER_DIRECT, mouse.change_host_idx)
    if current is None:
        log.debug("pending_host : lecture hôte souris impossible, prochaine tentative au reconnect")
        return False

    if current == target_host:
        log.debug("Sync confirmée : souris sur hôte %d ✓", target_host)
        state["pending_host"] = None
        return False

    log.warning(
        "Désync détectée : souris=hôte%d, attendu=hôte%d → correction...",
        current,
        target_host,
    )
    notify(f"Désync corrigée → hôte {target_host + 1}", "SwiGi")
    try:
        send_change_host(mouse.transport, DEVNUMBER_DIRECT, mouse.change_host_idx, target_host)
        log.info("Correction désync → hôte %d ✓", target_host)
        state["pending_host"] = None
    except (TransportError, OSError) as e:
        log.warning("Correction désync échouée : %s — nouvelle tentative au prochain reconnect", e)
    mouse.close()
    state["mouse"] = None
    return True


def run_daemon(
    kb: DeviceInfo,
    mouse: DeviceInfo,
    state: dict,
    stop_event: threading.Event,
) -> None:
    state["kb"] = kb.name
    state["mouse"] = mouse.name
    state.setdefault("pending_host", None)

    total_switches = 0
    last_response = time.time()
    last_switch_time = 0.0
    last_mouse_probe = 0.0
    mouse_hunt_deadline = 0.0   # probe rapide (1s) tant que ce timestamp n'est pas dépassé
    WATCHDOG_TIMEOUT = 10.0
    MOUSE_HUNT_WINDOW = 30.0    # secondes de probe rapide après reconnexion clavier
    MOUSE_HUNT_INTERVAL = 1.0
    MOUSE_PROBE_INTERVAL = 5.0

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
                _resync_pending_host_from_keyboard(kb, state)
                mouse_hunt_deadline = time.time() + MOUSE_HUNT_WINDOW
            mouse_new = find_device(DEVICE_TYPE_MOUSE)
            if mouse_new:
                mouse = mouse_new
                state["mouse"] = mouse.name
                log.info("Watchdog reconnexion souris : %s", mouse.name)
                if not _check_and_apply_pending_host(mouse, state):
                    _apply_bm_profile_if_needed(mouse.name)
            elif state.get("mouse") is None:
                log.info("Watchdog : souris introuvable — probe rapide actif (%ds)", int(MOUSE_HUNT_WINDOW))
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

            _resync_pending_host_from_keyboard(kb, state)
            mouse_hunt_deadline = time.time() + MOUSE_HUNT_WINDOW

            if mouse.transport.is_open:
                mouse.close()
            state["mouse"] = None
            log.debug("Reconnexion proactive de la souris...")
            new_mouse = find_device(DEVICE_TYPE_MOUSE)
            if new_mouse:
                mouse = new_mouse
                state["mouse"] = mouse.name
                log.info("Souris prête : %s", mouse.name)
                if not _check_and_apply_pending_host(mouse, state):
                    _apply_bm_profile_if_needed(mouse.name)
                    notify(f"{mouse.name} reconnectée", "Souris")
            else:
                log.info("Souris introuvable après reconnexion clavier — probe toutes les %ds pendant %ds",
                         int(MOUSE_HUNT_INTERVAL), int(MOUSE_HUNT_WINDOW))
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
                last_switch_time = time.time()
                log.info("─" * 50)
                log.info("★ Easy-Switch : %s → hôte %d", kb.name, target_host)

                if not mouse.transport.is_open:
                    log.debug("Transport souris fermé, reconnexion...")
                    new_mouse = find_device(DEVICE_TYPE_MOUSE)
                    if new_mouse:
                        mouse = new_mouse
                        state["mouse"] = mouse.name
                    else:
                        log.info("Souris indisponible — basculera au prochain Easy-Switch")
                        state["pending_host"] = (target_host, time.time() + _PENDING_HOST_TTL)
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
                    state["pending_host"] = (target_host, time.time() + _PENDING_HOST_TTL)
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
                    # Dans tous les cas : mémoriser l'hôte cible pour correction au reconnect
                    state["pending_host"] = (target_host, time.time() + _PENDING_HOST_TTL)
                    if state.get("mouse"):
                        mouse.close()
                        state["mouse"] = None

                break

            if sw_id == 0:
                log.debug("Notification : feat=0x%02X [%s]", feat, raw[:10].hex())

        time.sleep(0.01)

        # Sonde périodique : reconnecter la souris si absente et clavier OK
        in_hunt = time.time() < mouse_hunt_deadline
        probe_interval = MOUSE_HUNT_INTERVAL if in_hunt else MOUSE_PROBE_INTERVAL
        if state.get("mouse") is None and time.time() - last_mouse_probe > probe_interval:
            last_mouse_probe = time.time()
            new_mouse = find_device(DEVICE_TYPE_MOUSE)
            if new_mouse:
                if mouse.transport.is_open:
                    mouse.close()
                mouse = new_mouse
                state["mouse"] = mouse.name
                log.info("Souris reconnectée automatiquement : %s", mouse.name)
                if not _check_and_apply_pending_host(mouse, state):
                    _apply_bm_profile_if_needed(mouse.name)
                    notify(f"{mouse.name} reconnectée", "Souris")
            elif in_hunt:
                log.debug("Probe (hunt) : souris introuvable, retry dans %ds", int(MOUSE_HUNT_INTERVAL))

    log.info("Arrêt. Total : %d basculements.", total_switches)
    kb.close()
    mouse.close()
