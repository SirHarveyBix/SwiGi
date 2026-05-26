import dataclasses
import logging
import queue
import threading
import time
from contextlib import nullcontext

from swigi.constants import (
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    DEVNUMBER_DIRECT,
    MSG_LENGTHS,
    PING_MSG,
)
from swigi.constants import SYSTEM
from swigi.discovery import DeviceInfo, find_all_devices
from swigi.gui import notify, prefs, _prefs_lock
from swigi.protocol import get_current_host, send_change_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.daemon")

_PENDING_HOST_TTL = 60.0  # secondes avant abandon de la correction pending


# ── Structures d'événements inter-threads ─────────────────────────────────────

@dataclasses.dataclass
class _SwitchEvent:
    """Un clavier a demandé un basculement d'hôte."""
    target_host: int
    kb_name: str


@dataclasses.dataclass
class _KbReconnected:
    """Un clavier vient de se reconnecter."""
    kb_name: str


# ── Fonctions helper ──────────────────────────────────────────────────────────

def _apply_bm_profile_if_needed(mouse_name: str | None = None) -> None:
    """Applique le profil BetterMouse configuré si auto-apply est activé.

    No-op si BetterMouse absent, profil non configuré, ou toggle désactivé.
    Toujours silencieux en cas d'erreur (ne bloque jamais la boucle principale).
    """
    if SYSTEM != "Darwin":
        return
    with _prefs_lock:
        bm_auto = prefs.get("bm_auto_apply")
        bm_profile = prefs.get("bm_profile")
    if not bm_auto or not bm_profile:
        return
    try:
        from swigi.bettermouse import apply_profile
        apply_profile(bm_profile, mouse_name=mouse_name)
        notify(f"Profil {bm_profile} appliqué", "BetterMouse")
        log.info("BetterMouse : profil '%s' appliqué", bm_profile)
    except ValueError as e:
        log.warning("BetterMouse : profil ignoré (souris différente) : %s", e)
    except Exception as e:
        log.warning("BetterMouse : apply_profile échoué : %s", e)


def _resync_pending_host_from_keyboard(kb: DeviceInfo, state: dict) -> None:
    """Met à jour pending_host d'après l'hôte réel du clavier après reconnexion.

    Corrige le cas où un pending_host stale (du switch précédent) enverrait la
    souris sur le mauvais hôte lors du retour — surtout avec 3 hôtes ou 2 claviers.
    """
    # Ne pas recaler pending_host si suivi souris désactivé
    with _prefs_lock:
        mouse_follow = prefs.get("mouse_follow", True)
    if not mouse_follow:
        state["pending_host"] = None
        return

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
    # Ne pas corriger si le suivi est désactivé
    with _prefs_lock:
        mouse_follow = prefs.get("mouse_follow", True)
    if not mouse_follow:
        state["pending_host"] = None
        return False

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

    # Pendant le I/O (~500ms), un nouveau switch peut avoir modifié pending_host.
    # Si c'est le cas, abandonner : le nouveau switch est la vérité à jour.
    pending_now = state.get("pending_host")
    if pending_now is None or pending_now[0] != target_host:
        log.debug("pending_host modifié pendant I/O — abandon (switch plus récent en cours)")
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


def _find_kb_by_pid(pid: int) -> DeviceInfo | None:
    """Cherche un clavier par son PID exact.

    Utilisé lors de la reconnexion pour retrouver LE bon clavier (pas le premier venu).
    Ferme tous les autres candidats.
    Retourne None si aucun clavier avec ce PID n'est disponible.
    """
    candidates = find_all_devices(DEVICE_TYPE_KEYBOARD)
    result = None
    for kb in candidates:
        if kb.pid == pid and result is None:
            result = kb
        else:
            # Fermer les candidats qui ne correspondent pas (ou les doublons)
            kb.close()
    return result


def _send_to_all_mice(
    mice: list[DeviceInfo],
    target_host: int,
    state: dict,
    mouse_lock: threading.Lock,
) -> None:
    """Envoie CHANGE_HOST à toutes les souris disponibles.

    Thread-safe : acquiert mouse_lock avant d'accéder à la liste.
    Souris avec transport fermé : skippée (sera traitée au prochain reconnect).
    Met à jour state["pending_host"] et state["mice"].
    """
    with mouse_lock:
        for mouse in list(mice):
            if not mouse.transport.is_open:
                log.debug("[%s] Transport fermé — skip", mouse.name)
                continue
            try:
                send_change_host(
                    mouse.transport,
                    DEVNUMBER_DIRECT,
                    mouse.change_host_idx,
                    target_host,
                )
                log.info("★ CHANGE_HOST → %s → hôte %d", mouse.name, target_host)
                mouse.close()
            except (TransportError, OSError) as e:
                log.warning("[%s] CHANGE_HOST échoué : %s", mouse.name, e)
                mouse.close()

        # Vider la liste : probe_loop va retrouver les souris avec des handles frais.
        # Sans ce clear(), les DeviceInfo avec transport fermé bloquent la reconnexion
        # (leur PID reste dans existing_pids → nouvel handle fermé comme "doublon").
        mice.clear()
        state["pending_host"] = (target_host, time.time() + _PENDING_HOST_TTL)
        state["mouse"] = None
        state["mice"] = []


def _watch_keyboard(
    kb: DeviceInfo,
    event_q: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Thread dédié à la surveillance d'un clavier.

    - Ping + lecture des événements (fenêtre 80ms)
    - Watchdog 10s sans réponse → reconnexion par PID
    - Sur CHANGE_HOST → envoie _SwitchEvent dans event_q
    - Sur reconnect → envoie _KbReconnected dans event_q
    """
    prefix = f"[{kb.name}]"
    last_response = time.time()
    last_switch_time = 0.0
    WATCHDOG_TIMEOUT = 10.0

    log.info("%s Surveillance démarrée (PID=0x%04X)", prefix, kb.pid)

    while not stop_event.is_set():
        # ── Watchdog ──
        if time.time() - last_response > WATCHDOG_TIMEOUT:
            log.info("%s Watchdog : aucune réponse depuis %ds, reconnexion...",
                     prefix, int(WATCHDOG_TIMEOUT))
            kb.close()
            _lock = state.get("_state_lock") or nullcontext()
            with _lock:
                state["kbs"][kb.pid]["ok"] = False

            delay = 0.5
            max_delay = 5.0
            kb_new = None
            while not stop_event.is_set():
                time.sleep(delay)
                kb_new = _find_kb_by_pid(kb.pid)
                if kb_new is not None:
                    break
                delay = min(delay * 1.5, max_delay)
                log.debug("%s Reconnexion en cours (prochaine dans %.1fs)...", prefix, delay)

            if kb_new:
                kb = kb_new
                kb.name = kb.name  # nom potentiellement mis à jour
                prefix = f"[{kb.name}]"
                _lock = state.get("_state_lock") or nullcontext()
                with _lock:
                    state["kbs"][kb.pid] = {"name": kb.name, "ok": True}
                # Compatibilité GUI : mettre à jour le premier clavier actif
                _update_kb_state(state)
                log.info("%s Watchdog reconnexion OK", prefix)
                _resync_pending_host_from_keyboard(kb, state)
                hunt_trigger.set()
                event_q.put(_KbReconnected(kb.name))
            last_response = time.time()
            continue

        # ── Ping ──
        try:
            kb.transport.write(PING_MSG)
        except (TransportError, OSError):
            switch_triggered = time.time() - last_switch_time <= 3.0
            log.info("%s Déconnecté%s", prefix, " (post-switch)" if switch_triggered else "")
            if not switch_triggered:
                notify(f"{kb.name} déconnecté", "Clavier")
            kb.close()
            _lock = state.get("_state_lock") or nullcontext()
            with _lock:
                state["kbs"][kb.pid]["ok"] = False
            _update_kb_state(state)

            # Reconnexion : chercher CE clavier (même PID) avec backoff exponentiel
            delay = 0.5
            max_delay = 5.0
            kb_new = None
            while not stop_event.is_set():
                time.sleep(delay)
                kb_new = _find_kb_by_pid(kb.pid)
                if kb_new is not None:
                    break
                delay = min(delay * 1.5, max_delay)
                log.debug("%s Reconnexion en cours (prochaine dans %.1fs)...", prefix, delay)

            if kb_new is None:
                continue  # stop_event levé pendant reconnect — la boucle externe sort

            kb = kb_new
            prefix = f"[{kb.name}]"
            _lock = state.get("_state_lock") or nullcontext()
            with _lock:
                state["kbs"][kb.pid] = {"name": kb.name, "ok": True}
            _update_kb_state(state)
            log.info("%s Reconnexion OK", prefix)
            notify(f"{kb.name} reconnecté", "Clavier")
            last_response = time.time()

            _resync_pending_host_from_keyboard(kb, state)
            hunt_trigger.set()
            event_q.put(_KbReconnected(kb.name))
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
            if rid not in MSG_LENGTHS or len(raw) < MSG_LENGTHS[rid]:
                continue

            feat = raw[2]
            func = raw[3]
            sw_id = func & 0x0F
            last_response = time.time()

            # Notification CHANGE_HOST
            # Format HID++ 2.0 fn0x00 notification : raw[4]=numHosts, raw[5]=newHost (base 0)
            if feat == kb.change_host_idx and sw_id == 0 and len(raw) > 5:
                num_hosts = raw[4] if raw[4] > 0 else 3  # fallback 3 si firmware ne renseigne pas
                target_host = raw[5]
                if not (0 <= target_host < num_hosts):
                    log.warning(
                        "%s Hôte cible invalide : %d (numHosts=%d, ignoré)",
                        prefix, target_host, num_hosts,
                    )
                    continue
                last_switch_time = time.time()
                log.info("─" * 50)
                log.info("%s ★ Easy-Switch → hôte %d", prefix, target_host)
                event_q.put(_SwitchEvent(target_host, kb.name))
                break

            if sw_id == 0:
                log.debug("%s Notification : feat=0x%02X [%s]", prefix, feat, raw[:10].hex())

        time.sleep(0.01)

    log.info("%s Thread arrêté.", prefix)
    kb.close()


def _mice_probe_loop(
    mice: list[DeviceInfo],
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
    mouse_lock: threading.Lock,
) -> None:
    """Thread dédié à la surveillance et reconnexion des souris.

    - En mode hunt (après un reconnect clavier) : probe toutes les 1s pendant 30s
    - Mode normal : probe toutes les 5s
    - Ajoute les nouvelles souris trouvées, retire celles qui ont disparu
    - Applique pending_host aux nouvelles souris connectées
    """
    MOUSE_HUNT_INTERVAL = 1.0
    MOUSE_PROBE_INTERVAL = 5.0
    MOUSE_HUNT_WINDOW = 30.0

    hunt_deadline = 0.0

    log.debug("Probe souris : thread démarré")

    while not stop_event.is_set():
        # Timeout adaptatif : 1s en hunt (détection rapide post-switch), 5s en veille.
        # BUG FIX : le timeout doit être calculé AVANT wait(), sinon le tour suivant
        # attend toujours 5s même en hunt mode (bug : intervalle réel = 6s au lieu de 1s).
        in_hunt = time.time() < hunt_deadline
        timeout = MOUSE_HUNT_INTERVAL if in_hunt else MOUSE_PROBE_INTERVAL
        triggered = hunt_trigger.wait(timeout=timeout)
        if triggered:
            hunt_trigger.clear()
            hunt_deadline = time.time() + MOUSE_HUNT_WINDOW
            log.debug("Probe souris : mode hunt activé (%ds)", int(MOUSE_HUNT_WINDOW))

        if stop_event.is_set():
            break

        in_hunt = time.time() < hunt_deadline

        # Probe les souris disponibles
        found = find_all_devices(DEVICE_TYPE_MOUSE)
        found_by_pid = {m.pid: m for m in found}

        # Pass 1 : mises à jour liste (rapide, no I/O) + collecte nouvelles souris
        new_mice = []
        with mouse_lock:
            for new_m in found:
                existing = next((m for m in mice if m.pid == new_m.pid), None)
                if existing is None:
                    mice.append(new_m)
                    new_mice.append(new_m)
                elif not existing.transport.is_open:
                    mice.remove(existing)
                    mice.append(new_m)
                    new_mice.append(new_m)
                else:
                    # Transport déjà ouvert — fermer le doublon
                    new_m.close()

            # Retirer les souris mortes non retrouvées par find_all_devices
            for m in [x for x in list(mice) if not x.transport.is_open and x.pid not in found_by_pid]:
                log.info("Souris retirée : %s (plus disponible)", m.name)
                mice.remove(m)

        # Pass 2 : HID I/O hors du lock (get_current_host peut prendre ~500ms)
        for new_m in new_mice:
            log.info("Souris détectée : %s (PID=0x%04X)", new_m.name, new_m.pid)
            notify(f"{new_m.name} connectée", "Souris")
            if not _check_and_apply_pending_host(new_m, state):
                _apply_bm_profile_if_needed(new_m.name)

        # Pass 2b : vérifier pending_host sur les souris existantes (pas seulement nouvelles).
        # Corrige le cas où get_current_host retourne None au premier essai (BT timing)
        # ou quand pending_host est recalé par _resync_pending_host_from_keyboard après
        # que la souris est déjà dans mice_list.
        if state.get("pending_host") and not new_mice:
            with mouse_lock:
                existing_open = [m for m in mice if m.transport.is_open]
            for m in existing_open:
                if _check_and_apply_pending_host(m, state):
                    break  # transport fermé, sera redécouvert au prochain cycle

        # Pass 3 : mise à jour state sous mouse_lock
        with mouse_lock:
            active = [m for m in mice if m.transport.is_open]
            state["mice"] = [m.name for m in active]
            state["mouse"] = active[0].name if active else None

        if in_hunt and not mice:
            log.debug("Probe (hunt) : souris introuvable, retry dans %ds", int(MOUSE_HUNT_INTERVAL))

    log.debug("Probe souris : thread arrêté")
    with mouse_lock:
        for m in mice:
            m.close()


def _update_kb_state(state: dict) -> None:
    """Met à jour state["kb"] avec le nom du premier clavier actif.

    Compatibilité GUI : state["kb"] doit rester valide pour le timer de rafraîchissement.
    """
    _lock = state.get("_state_lock") or nullcontext()
    with _lock:
        kbs = dict(state.get("kbs", {}))
    for pid_data in kbs.values():
        if pid_data.get("ok"):
            state["kb"] = pid_data["name"]
            return
    state["kb"] = None


# ── Point d'entrée principal ──────────────────────────────────────────────────

def run_daemon(
    keyboards: list[DeviceInfo],
    mice: list[DeviceInfo],
    state: dict,
    stop_event: threading.Event,
) -> None:
    """Lance le daemon multi-clavier multi-souris.

    - Un thread _watch_keyboard par clavier
    - Un thread _mice_probe_loop pour toutes les souris
    - Boucle principale : lit la queue d'événements et dispatche
    """
    # Initialiser l'état
    state["_state_lock"] = threading.Lock()
    state["kbs"] = {kb.pid: {"name": kb.name, "ok": True} for kb in keyboards}
    state["mouse"] = mice[0].name if mice else None
    state["mice"] = [m.name for m in mice]
    state.setdefault("pending_host", None)
    state.setdefault("switches", 0)

    # Compatibilité GUI : state["kb"] = nom du premier clavier
    state["kb"] = keyboards[0].name if keyboards else None

    # Structures de communication inter-threads
    event_q: queue.Queue = queue.Queue()
    mouse_lock = threading.Lock()
    hunt_trigger = threading.Event()

    # Copie mutable de la liste des souris (partagée entre threads via mouse_lock)
    mice_list = list(mice)

    # Spawner un thread par clavier
    kb_threads = []
    for kb in keyboards:
        t = threading.Thread(
            target=_watch_keyboard,
            args=(kb, event_q, state, stop_event, hunt_trigger),
            name=f"kb-{kb.pid:04X}",
            daemon=True,
        )
        t.start()
        kb_threads.append(t)
        log.info("Thread clavier démarré : %s (PID=0x%04X)", kb.name, kb.pid)

    # Thread probe souris
    probe_thread = threading.Thread(
        target=_mice_probe_loop,
        args=(mice_list, state, stop_event, hunt_trigger, mouse_lock),
        name="mice-probe",
        daemon=True,
    )
    probe_thread.start()
    log.info("Thread probe souris démarré (%d souris initiales)", len(mice_list))

    # ── Boucle principale : dispatcher d'événements ──
    while not stop_event.is_set():
        try:
            event = event_q.get(timeout=1.0)
        except queue.Empty:
            continue

        if isinstance(event, _SwitchEvent):
            log.info("★ Switch reçu de [%s] → hôte %d", event.kb_name, event.target_host)

            # Vérifier si le suivi de la souris est activé
            with _prefs_lock:
                mouse_follow = prefs.get("mouse_follow", True)

            if mouse_follow:
                _send_to_all_mice(mice_list, event.target_host, state, mouse_lock)
            else:
                log.info("Suivi souris désactivé — CHANGE_HOST non envoyé")
                state["pending_host"] = None

            state["switches"] = state.get("switches", 0) + 1
            hunt_trigger.set()  # probe rapide pour reconnecter les souris après switch

        elif isinstance(event, _KbReconnected):
            log.info("Clavier reconnecté : %s", event.kb_name)
            _update_kb_state(state)

    log.info("Arrêt du daemon. Total : %d basculements.", state.get("switches", 0))

    # Attendre les threads
    for t in kb_threads:
        t.join(timeout=3)
    probe_thread.join(timeout=3)
