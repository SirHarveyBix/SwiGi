import dataclasses
import logging
import queue
import threading
import time
from contextlib import nullcontext

from swigi.constants import (
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    DEVICE_NUMBER_DIRECT,
    MSG_LENGTHS,
    PING_MESSAGE,
)
from swigi.constants import SYSTEM
from swigi.discovery import DeviceInfo, find_all_devices
from swigi.gui import notify, prefs, _prefs_lock
from swigi.protocol import get_current_host, send_change_host
from swigi.transport import TransportError

log = logging.getLogger("swigi.daemon")

_PENDING_HOST_TTL = 60.0  # secondes avant abandon de la correction pending

# Délai de stabilité après reconnexion clavier (rejet des connexions fantômes BT).
# Un clavier en transit vers un autre Mac peut être brièvement connectable depuis ce Mac.
# Patchable à 0.0 dans les tests.
_KEYBOARD_STABILITY_SECONDS = 2.0

# Fenêtre fantôme : disconnect < cette durée après reconnexion (sans switch) → pending_host effacé.
_KEYBOARD_PHANTOM_WINDOW = 5.0


# ── Structures d'événements inter-threads ─────────────────────────────────────

@dataclasses.dataclass
class _SwitchEvent:
    """Un clavier a demandé un basculement d'hôte."""
    target_host: int
    keyboard_name: str


@dataclasses.dataclass
class _KeyboardReconnected:
    """Un clavier vient de se reconnecter."""
    keyboard_name: str


# ── Fonctions helper ──────────────────────────────────────────────────────────

def _apply_better_mouse_profile_if_needed(mouse_name: str | None = None) -> None:
    """Applique le profil BetterMouse configuré si auto-apply est activé.

    No-op si BetterMouse absent, profil non configuré, ou toggle désactivé.
    Toujours silencieux en cas d'erreur (ne bloque jamais la boucle principale).
    """
    if SYSTEM != "Darwin":
        return
    with _prefs_lock:
        better_mouse_auto = prefs.get("better_mouse_auto_apply")
        better_mouse_profile = prefs.get("better_mouse_profile")
    if not better_mouse_auto or not better_mouse_profile:
        return
    try:
        from swigi.bettermouse import apply_profile
        apply_profile(better_mouse_profile, mouse_name=mouse_name)
        notify(f"Profil {better_mouse_profile} appliqué", "BetterMouse")
        log.info("BetterMouse : profil '%s' appliqué", better_mouse_profile)
    except ValueError as error:
        log.warning("BetterMouse : profil ignoré (souris différente) : %s", error)
    except Exception as error:
        log.warning("BetterMouse : apply_profile échoué : %s", error)


_RESYNC_RETRIES = 3
_RESYNC_RETRY_DELAY = 0.15  # secondes — laisse le stack BT se stabiliser après reconnexion

# Constantes probe souris — module-level pour être patchables dans les tests.
_MOUSE_HUNT_INTERVAL = 1.0   # secondes entre probes en mode hunt
_MOUSE_PROBE_INTERVAL = 5.0  # secondes entre probes en mode normal
_MOUSE_HUNT_WINDOW = 30.0    # durée du mode hunt après un trigger

# Constantes reconnexion clavier — module-level pour être patchables dans les tests.
_KEYBOARD_RECONNECT_INITIAL_DELAY = 0.5  # délai initial de reconnexion (backoff exponentiel)
_KEYBOARD_RECONNECT_MAX_DELAY = 5.0      # délai maximum de reconnexion


def _resync_pending_host_from_keyboard(keyboard: DeviceInfo, state: dict) -> None:
    """Met à jour pending_host d'après l'hôte réel du clavier après reconnexion.

    Corrige le cas où un pending_host stale (du switch précédent) enverrait la
    souris sur le mauvais hôte lors du retour — surtout avec 3 hôtes ou 2 claviers.
    """
    with _prefs_lock:
        mouse_follow = prefs.get("mouse_follow", True)
    if not mouse_follow:
        state["pending_host"] = None
        return

    # Le stack BT macOS peut prendre 150–300ms après reconnexion avant que
    # getHostInfo réponde correctement — retry avec backoff linéaire.
    # Snapshot de pending_host avant I/O : si un switch survient pendant les retries,
    # la valeur change d'objet → comparaison d'identité détecte la modification.
    pending_before_io = state.get("pending_host")
    keyboard_host = None
    for attempt in range(_RESYNC_RETRIES):
        if attempt > 0:
            time.sleep(_RESYNC_RETRY_DELAY)
        try:
            keyboard_host = get_current_host(keyboard.transport, DEVICE_NUMBER_DIRECT, keyboard.change_host_index)
        except (TransportError, OSError) as error:
            log.debug("resync essai %d/%d : TransportError : %s", attempt + 1, _RESYNC_RETRIES, error)
            continue
        log.debug("resync essai %d/%d : keyboard_host=%s", attempt + 1, _RESYNC_RETRIES, keyboard_host)
        if keyboard_host is not None:
            break

    if keyboard_host is not None:
        # Ne pas écraser un pending_host plus récent issu d'un switch pendant l'I/O.
        # _send_to_all_mice crée un nouveau tuple → l'identité d'objet change.
        if state.get("pending_host") is pending_before_io:
            state["pending_host"] = (keyboard_host, time.time() + _PENDING_HOST_TTL)
            log.debug("pending_host recalé sur hôte clavier : %d", keyboard_host)
        else:
            log.debug("pending_host modifié pendant resync I/O — resync ignorée (switch plus récent)")
    else:
        state["pending_host"] = None
        log.warning("pending_host effacé — hôte clavier illisible après %d essais", _RESYNC_RETRIES)


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

    try:
        current = get_current_host(mouse.transport, DEVICE_NUMBER_DIRECT, mouse.change_host_index)
    except (TransportError, OSError) as error:
        log.debug("pending_host : transport souris mort pendant lecture hôte : %s", error)
        mouse.close()
        state["mouse"] = None
        return True
    log.debug("pending_host check : %s current=%s target=%d", mouse.name, current, target_host)
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
        send_change_host(mouse.transport, DEVICE_NUMBER_DIRECT, mouse.change_host_index, target_host)
        log.info("Correction désync → hôte %d ✓", target_host)
        state["pending_host"] = None
    except (TransportError, OSError) as error:
        log.warning("Correction désync échouée : %s — nouvelle tentative au prochain reconnect", error)
    mouse.close()
    state["mouse"] = None
    return True


def _find_keyboard_by_product_id(product_id: int) -> DeviceInfo | None:
    """Cherche un clavier par son Product ID exact.

    Utilisé lors de la reconnexion pour retrouver LE bon clavier (pas le premier venu).
    Ferme tous les autres candidats.
    Retourne None si aucun clavier avec ce Product ID n'est disponible.
    """
    candidates = find_all_devices(DEVICE_TYPE_KEYBOARD)
    result = None
    for keyboard in candidates:
        if keyboard.product_id == product_id and result is None:
            result = keyboard
        else:
            # Fermer les candidats qui ne correspondent pas (ou les doublons)
            keyboard.close()
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
        log.debug("_send_to_all_mice : mice_list=%s target=%d",
                  [f"{mouse.name}(open={mouse.transport.is_open})" for mouse in mice], target_host)
        for mouse in list(mice):
            if not mouse.transport.is_open:
                log.debug("[%s] Transport fermé — skip", mouse.name)
                continue
            try:
                send_change_host(
                    mouse.transport,
                    DEVICE_NUMBER_DIRECT,
                    mouse.change_host_index,
                    target_host,
                )
                log.info("★ CHANGE_HOST → %s → hôte %d", mouse.name, target_host)
                mouse.close()
            except (TransportError, OSError) as error:
                log.warning("[%s] CHANGE_HOST échoué : %s", mouse.name, error)
                mouse.close()

        # Vider la liste : probe_loop va retrouver les souris avec des handles frais.
        # Sans ce clear(), les DeviceInfo avec transport fermé bloquent la reconnexion
        # (leur Product ID reste dans existing_product_ids → nouvel handle fermé comme "doublon").
        mice.clear()
        state["pending_host"] = (target_host, time.time() + _PENDING_HOST_TTL)
        state["mouse"] = None
        state["mice"] = []


def _watch_keyboard(
    keyboard: DeviceInfo,
    event_queue: queue.Queue,
    state: dict,
    stop_event: threading.Event,
    hunt_trigger: threading.Event,
) -> None:
    """Thread dédié à la surveillance d'un clavier.

    - Ping + lecture des événements (fenêtre 80ms)
    - Watchdog 10s sans réponse → reconnexion par Product ID
    - Sur CHANGE_HOST → envoie _SwitchEvent dans event_queue
    - Sur reconnect → envoie _KeyboardReconnected dans event_queue
    """
    prefix = f"[{keyboard.name}]"
    last_response = time.time()
    last_switch_time = 0.0
    keyboard_reconnected_at: float | None = None
    WATCHDOG_TIMEOUT = 10.0

    log.info("%s Surveillance démarrée (Product ID=0x%04X)", prefix, keyboard.product_id)

    while not stop_event.is_set():
        # ── Watchdog ──
        if time.time() - last_response > WATCHDOG_TIMEOUT:
            log.info("%s Watchdog : aucune réponse depuis %ds, reconnexion...",
                     prefix, int(WATCHDOG_TIMEOUT))
            keyboard.close()
            with state.get("_state_lock") or nullcontext():
                state["keyboards"][keyboard.product_id]["ok"] = False

            delay = _KEYBOARD_RECONNECT_INITIAL_DELAY
            new_keyboard = None
            while not stop_event.is_set():
                time.sleep(delay)
                new_keyboard = _find_keyboard_by_product_id(keyboard.product_id)
                if new_keyboard is not None:
                    if _KEYBOARD_STABILITY_SECONDS > 0:
                        time.sleep(_KEYBOARD_STABILITY_SECONDS)
                    try:
                        new_keyboard.transport.write(PING_MESSAGE)
                        break  # stable
                    except (TransportError, OSError):
                        log.debug("%s Connexion fantôme watchdog — réessai", prefix)
                        new_keyboard.close()
                        new_keyboard = None
                delay = min(delay * 1.5, _KEYBOARD_RECONNECT_MAX_DELAY)
                log.debug("%s Reconnexion en cours (prochaine dans %.1fs)...", prefix, delay)

            if new_keyboard:
                keyboard = new_keyboard
                prefix = f"[{keyboard.name}]"
                with state.get("_state_lock") or nullcontext():
                    state["keyboards"][keyboard.product_id] = {"name": keyboard.name, "ok": True}
                # Compatibilité GUI : mettre à jour le premier clavier actif
                _update_keyboard_state(state)
                keyboard_reconnected_at = time.time()
                log.info("%s Watchdog reconnexion OK", prefix)
                _resync_pending_host_from_keyboard(keyboard, state)
                hunt_trigger.set()
                event_queue.put(_KeyboardReconnected(keyboard.name))
            last_response = time.time()
            continue

        # ── Ping ──
        try:
            keyboard.transport.write(PING_MESSAGE)
        except (TransportError, OSError):
            # Drain buffer : notification CHANGE_HOST peut être en file noyau
            # juste avant la déconnexion BT (keyboard sends event then drops).
            try:
                for _ in range(8):
                    raw_bytes = keyboard.transport.read(timeout=5)
                    if raw_bytes is None or len(raw_bytes) < 4:
                        break
                    if raw_bytes[0] not in MSG_LENGTHS or len(raw_bytes) < MSG_LENGTHS[raw_bytes[0]]:
                        continue
                    feature_index = raw_bytes[2]
                    software_id = raw_bytes[3] & 0x0F
                    if feature_index == keyboard.change_host_index and software_id == 0 and len(raw_bytes) > 5:
                        num_hosts = raw_bytes[4] if raw_bytes[4] > 0 else 3
                        target_host = raw_bytes[5]
                        if 0 <= target_host < num_hosts:
                            last_switch_time = time.time()
                            log.info("%s ★ Easy-Switch (buffer) → hôte %d", prefix, target_host)
                            event_queue.put(_SwitchEvent(target_host, keyboard.name))
                        break
            except (TransportError, OSError):
                pass

            switch_triggered = time.time() - last_switch_time <= 3.0
            log.info("%s Déconnecté%s", prefix, " (post-switch)" if switch_triggered else "")
            if not switch_triggered:
                notify(f"{keyboard.name} déconnecté", "Clavier")

            # Détection connexion fantôme : disconnect trop rapide après reconnexion,
            # sans switch → pending_host corrompu par l'hôte transitoire du clavier.
            if not switch_triggered and keyboard_reconnected_at is not None:
                elapsed = time.time() - keyboard_reconnected_at
                if elapsed < _KEYBOARD_PHANTOM_WINDOW:
                    log.warning(
                        "%s Connexion fantôme (%.1fs après reconnexion) → pending_host effacé",
                        prefix, elapsed,
                    )
                    state["pending_host"] = None
            keyboard_reconnected_at = None

            keyboard.close()
            with state.get("_state_lock") or nullcontext():
                state["keyboards"][keyboard.product_id]["ok"] = False
            _update_keyboard_state(state)

            # Reconnexion : chercher CE clavier (même Product ID) avec backoff exponentiel
            delay = _KEYBOARD_RECONNECT_INITIAL_DELAY
            new_keyboard = None
            while not stop_event.is_set():
                time.sleep(delay)
                new_keyboard = _find_keyboard_by_product_id(keyboard.product_id)
                if new_keyboard is not None:
                    if _KEYBOARD_STABILITY_SECONDS > 0:
                        time.sleep(_KEYBOARD_STABILITY_SECONDS)
                    try:
                        new_keyboard.transport.write(PING_MESSAGE)
                        break  # stable
                    except (TransportError, OSError):
                        log.debug("%s Connexion fantôme (< %.0fs) — réessai", prefix, _KEYBOARD_STABILITY_SECONDS)
                        new_keyboard.close()
                        new_keyboard = None
                delay = min(delay * 1.5, _KEYBOARD_RECONNECT_MAX_DELAY)
                log.debug("%s Reconnexion en cours (prochaine dans %.1fs)...", prefix, delay)

            if new_keyboard is None:
                continue  # stop_event levé pendant reconnect — la boucle externe sort

            keyboard = new_keyboard
            prefix = f"[{keyboard.name}]"
            with state.get("_state_lock") or nullcontext():
                state["keyboards"][keyboard.product_id] = {"name": keyboard.name, "ok": True}
            _update_keyboard_state(state)
            log.info("%s Reconnexion OK", prefix)
            notify(f"{keyboard.name} reconnecté", "Clavier")
            last_response = time.time()
            keyboard_reconnected_at = time.time()

            _resync_pending_host_from_keyboard(keyboard, state)
            hunt_trigger.set()
            event_queue.put(_KeyboardReconnected(keyboard.name))
            continue

        # ── Lecture réponses (fenêtre 80ms) ──
        raw_bytes = None
        deadline = time.time() + 0.08
        while time.time() < deadline and not stop_event.is_set():
            try:
                raw_bytes = keyboard.transport.read(timeout=25)
            except (TransportError, OSError):
                break

            if raw_bytes is None or len(raw_bytes) < 4:
                continue
            report_id = raw_bytes[0]
            if report_id not in MSG_LENGTHS or len(raw_bytes) < MSG_LENGTHS[report_id]:
                continue

            feature_index = raw_bytes[2]
            function_id = raw_bytes[3]
            software_id = function_id & 0x0F
            last_response = time.time()

            # Notification CHANGE_HOST
            # Format HID++ 2.0 fn0x00 notification : raw_bytes[4]=numHosts, raw_bytes[5]=newHost (base 0)
            if feature_index == keyboard.change_host_index and software_id == 0 and len(raw_bytes) > 5:
                num_hosts = raw_bytes[4] if raw_bytes[4] > 0 else 3  # fallback 3 si firmware ne renseigne pas
                target_host = raw_bytes[5]
                if not (0 <= target_host < num_hosts):
                    log.warning(
                        "%s Hôte cible invalide : %d (numHosts=%d, ignoré)",
                        prefix, target_host, num_hosts,
                    )
                    continue
                last_switch_time = time.time()
                log.info("─" * 50)
                log.info("%s ★ Easy-Switch → hôte %d", prefix, target_host)
                event_queue.put(_SwitchEvent(target_host, keyboard.name))
                break

            if software_id == 0:
                log.debug("%s Notification : feature_index=0x%02X [%s]", prefix, feature_index, raw_bytes[:10].hex())

        if raw_bytes is None:
            time.sleep(0.01)

    log.info("%s Thread arrêté.", prefix)
    keyboard.close()


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
    hunt_deadline = 0.0

    log.debug("Probe souris : thread démarré")

    while not stop_event.is_set():
        # Timeout adaptatif : 1s en hunt (détection rapide post-switch), 5s en veille.
        in_hunt = time.time() < hunt_deadline
        timeout = _MOUSE_HUNT_INTERVAL if in_hunt else _MOUSE_PROBE_INTERVAL
        triggered = hunt_trigger.wait(timeout=timeout)
        if triggered:
            hunt_trigger.clear()
            hunt_deadline = time.time() + _MOUSE_HUNT_WINDOW
            log.debug("Probe souris : mode hunt activé (%ds)", int(_MOUSE_HUNT_WINDOW))

        if stop_event.is_set():
            break

        in_hunt = time.time() < hunt_deadline

        # Probe les souris disponibles
        found = find_all_devices(DEVICE_TYPE_MOUSE)
        found_by_product_id = {mouse.product_id: mouse for mouse in found}
        log.debug("probe: found=%s mice_list=%s pending=%s hunt=%s",
                  [f"0x{mouse.product_id:04X}" for mouse in found],
                  [f"{mouse.name}(open={mouse.transport.is_open})" for mouse in mice],
                  state.get("pending_host"),
                  in_hunt)

        # Pass 1 : mises à jour liste (rapide, no I/O) + collecte nouvelles souris
        new_mice = []
        with mouse_lock:
            for new_mouse in found:
                existing = next((mouse for mouse in mice if mouse.product_id == new_mouse.product_id), None)
                if existing is None:
                    mice.append(new_mouse)
                    new_mice.append(new_mouse)
                elif not existing.transport.is_open:
                    mice.remove(existing)
                    mice.append(new_mouse)
                    new_mice.append(new_mouse)
                else:
                    # Transport déjà ouvert — fermer le doublon
                    new_mouse.close()

            # Retirer les souris mortes non retrouvées par find_all_devices
            dead = [x for x in mice if not x.transport.is_open and x.product_id not in found_by_product_id]
            for mouse in dead:
                log.info("Souris retirée : %s (plus disponible)", mouse.name)
            mice[:] = [x for x in mice if x not in dead]

        # Pass 2 : HID I/O hors du lock (get_current_host peut prendre ~500ms)
        for new_mouse in new_mice:
            log.info("Souris détectée : %s (Product ID=0x%04X)", new_mouse.name, new_mouse.product_id)
            notify(f"{new_mouse.name} connectée", "Souris")
            if not _check_and_apply_pending_host(new_mouse, state):
                _apply_better_mouse_profile_if_needed(new_mouse.name)

        # Pass 2b : vérifier pending_host sur les souris existantes (pas seulement nouvelles).
        # Corrige le cas où get_current_host retourne None au premier essai (BT timing)
        # ou quand pending_host est recalé par _resync_pending_host_from_keyboard après
        # que la souris est déjà dans mice_list.
        if state.get("pending_host") and not new_mice:
            with mouse_lock:
                existing_open = [mouse for mouse in mice if mouse.transport.is_open]
            for mouse in existing_open:
                if _check_and_apply_pending_host(mouse, state):
                    break  # transport fermé, sera redécouvert au prochain cycle

        # Pass 3 : mise à jour state sous mouse_lock
        with mouse_lock:
            active = [mouse for mouse in mice if mouse.transport.is_open]
            state["mice"] = [mouse.name for mouse in active]
            state["mouse"] = active[0].name if active else None

        if in_hunt and not mice:
            log.debug("Probe (hunt) : souris introuvable, retry dans %ds", int(_MOUSE_HUNT_INTERVAL))

    log.debug("Probe souris : thread arrêté")
    with mouse_lock:
        for mouse in mice:
            mouse.close()


def _update_keyboard_state(state: dict) -> None:
    """Met à jour state["keyboard"] avec le nom du premier clavier actif.

    Compatibilité GUI : state["keyboard"] doit rester valide pour le timer de rafraîchissement.
    """
    with state.get("_state_lock") or nullcontext():
        keyboards = dict(state.get("keyboards", {}))
        new_keyboard = None
        for product_id_data in keyboards.values():
            if product_id_data.get("ok"):
                new_keyboard = product_id_data["name"]
                break
        state["keyboard"] = new_keyboard


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
    state["keyboards"] = {keyboard.product_id: {"name": keyboard.name, "ok": True} for keyboard in keyboards}
    state["mouse"] = mice[0].name if mice else None
    state["mice"] = [mouse.name for mouse in mice]
    state.setdefault("pending_host", None)
    state.setdefault("switches", 0)

    # Compatibilité GUI : state["keyboard"] = nom du premier clavier
    state["keyboard"] = keyboards[0].name if keyboards else None

    # Structures de communication inter-threads
    event_queue: queue.Queue = queue.Queue()
    mouse_lock = threading.Lock()
    hunt_trigger = threading.Event()

    # Copie mutable de la liste des souris (partagée entre threads via mouse_lock)
    mice_list = list(mice)

    # Spawner un thread par clavier
    keyboard_threads = []
    for keyboard in keyboards:
        thread = threading.Thread(
            target=_watch_keyboard,
            args=(keyboard, event_queue, state, stop_event, hunt_trigger),
            name=f"keyboard-{keyboard.product_id:04X}",
            daemon=True,
        )
        thread.start()
        keyboard_threads.append(thread)
        log.info("Thread clavier démarré : %s (Product ID=0x%04X)", keyboard.name, keyboard.product_id)

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
            event = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if isinstance(event, _SwitchEvent):
            log.info("★ Switch reçu de [%s] → hôte %d", event.keyboard_name, event.target_host)

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

        elif isinstance(event, _KeyboardReconnected):
            log.info("Clavier reconnecté : %s", event.keyboard_name)
            _update_keyboard_state(state)

    log.info("Arrêt du daemon. Total : %d basculements.", state.get("switches", 0))

    # Attendre les threads
    for thread in keyboard_threads:
        thread.join(timeout=3)
    probe_thread.join(timeout=3)
