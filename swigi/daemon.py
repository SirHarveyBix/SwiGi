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

_PENDING_HOST_TTL = 12.0      # couvre _MANUAL_SWITCH_GRACE (8s) + marges BT LE

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


_RESYNC_RETRIES = 5
_RESYNC_RETRY_DELAY = (
    0.15  # secondes — laisse le stack BT se stabiliser après reconnexion
)

# Constantes probe souris — module-level pour être patchables dans les tests.
_MOUSE_HUNT_INTERVAL = 1.0         # secondes entre probes en mode hunt
_MOUSE_PROBE_INTERVAL = 5.0        # secondes entre probes en mode normal
_MOUSE_HUNT_WINDOW = 30.0          # durée du mode hunt après un trigger
_MANUAL_SWITCH_GRACE = 8.0         # délai avant lequel un reconnect souris peut être un switch manuel
_CHANGE_HOST_CONFIRM_WINDOW = 12.0  # fenêtre pour confirmer switch réussi (aucune souris visible)

# Constantes reconnexion clavier — module-level pour être patchables dans les tests.
_KEYBOARD_RECONNECT_INITIAL_DELAY = (
    0.5  # délai initial de reconnexion (backoff exponentiel)
)
_KEYBOARD_RECONNECT_MAX_DELAY = 5.0  # délai maximum de reconnexion


def _resync_pending_host_from_keyboard(keyboard: DeviceInfo, state: dict) -> None:
    """Synchronise pending_host sur l'hôte actuel du clavier après reconnexion.

    Lit l'hôte du clavier via getHostInfo et met à jour pending_host en conséquence.
    Ceci garantit que la souris suit le clavier quelle que soit l'origine de la
    reconnexion (switch Easy-Switch, retour sur ce Mac, ou reconnexion BT).

    La protection contre les switches manuels de la souris est dans
    _check_and_apply_pending_host (grace period + had_mice guard), pas ici.
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
            keyboard_host = get_current_host(
                keyboard.transport,
                DEVICE_NUMBER_DIRECT,
                keyboard.change_host_index,
                timeout=1000,  # plus long pour permettre au stack BT de se stabiliser
            )
        except (TransportError, OSError) as error:
            log.debug(
                "resync essai %d/%d : TransportError : %s",
                attempt + 1,
                _RESYNC_RETRIES,
                error,
            )
            continue
        log.debug(
            "resync essai %d/%d : keyboard_host=%s",
            attempt + 1,
            _RESYNC_RETRIES,
            keyboard_host,
        )
        if keyboard_host is not None:
            break

    if keyboard_host is not None:
        # Ne pas écraser un pending_host plus récent issu d'un switch pendant l'I/O.
        # _send_to_all_mice crée un nouveau tuple → l'identité d'objet change.
        if state.get("pending_host") is pending_before_io:
            was_none = pending_before_io is None
            state["pending_host"] = (keyboard_host, time.time() + _PENDING_HOST_TTL)
            if was_none:
                log.info("Clavier sur hôte %d → souris à synchroniser", keyboard_host + 1)
            else:
                log.info("pending_host recalé sur hôte clavier : %d", keyboard_host + 1)
        else:
            log.debug(
                "pending_host modifié pendant resync I/O — resync ignorée (switch plus récent)"
            )
    else:
        # Resync impossible — conserver pending_host existant si présent.
        # Un pending stale est préférable à perdre l'info de cible.
        existing = state.get("pending_host")
        if existing is None:
            state["pending_host"] = None
            log.warning(
                "pending_host reste None — hôte clavier illisible après %d essais",
                _RESYNC_RETRIES,
            )
        else:
            log.warning(
                "hôte clavier illisible après %d essais — pending_host conservé"
                " (host=%d, expire dans %.0fs)",
                _RESYNC_RETRIES,
                existing[0],
                max(0.0, existing[1] - time.time()),
            )
            # Ne pas modifier pending_host


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
        log.info(
            "pending_host expiré (hôte %d avait %.0fs) — abandon",
            target_host + 1,
            _PENDING_HOST_TTL,
        )
        state["pending_host"] = None
        return False

    try:
        current = get_current_host(
            mouse.transport, DEVICE_NUMBER_DIRECT, mouse.change_host_index
        )
    except (TransportError, OSError) as error:
        log.debug(
            "pending_host : transport souris mort pendant lecture hôte : %s", error
        )
        mouse.close()
        state["mouse"] = None
        # Retirer de mice si présente (cohérence avant que probe_loop la retire au cycle suivant)
        if mouse.name in state.get("mice", []):
            state["mice"] = [m for m in state.get("mice", []) if m != mouse.name]
        return True
    log.info(
        "Vérif sync : %s hôte actuel=%s cible=%d",
        mouse.name,
        (current + 1) if current is not None else "?",
        target_host + 1,
    )
    if current is None:
        log.debug(
            "pending_host : lecture hôte souris impossible, prochaine tentative au reconnect"
        )
        return False

    # Pendant le I/O (~500ms), un nouveau switch peut avoir modifié pending_host.
    # Si c'est le cas, abandonner : le nouveau switch est la vérité à jour.
    pending_now = state.get("pending_host")
    if pending_now is None or pending_now[0] != target_host:
        log.debug(
            "pending_host modifié pendant I/O — abandon (switch plus récent en cours)"
        )
        return False

    # Switch manuel : si des souris étaient connectées au switch (had_mice=True) ET que la
    # souris actuelle est sur le mauvais hôte ET que le délai grace est dépassé → switch manuel.
    # Cas had_mice=False : aucune souris n'était là au switch → toujours corriger dans le TTL
    # (la souris se connecte en retard, ce n'est PAS un switch manuel).
    # Pass 1b efface pending dès que la souris disparaît (switch réussi) — cette vérification
    # ne s'applique qu'aux cas où Pass 1b n'a pas pu se déclencher (BT kernel delay, etc.)
    had_mice_at_switch = state.get("last_change_host_had_mice", True)
    last_ch = state.get("last_change_host_at", 0.0)
    elapsed_since_ch = time.time() - last_ch
    if had_mice_at_switch and current != target_host and elapsed_since_ch > _MANUAL_SWITCH_GRACE:
        log.info(
            "Switch manuel détecté : souris sur hôte %d (attendu hôte %d), "
            "%.0fs depuis dernier CHANGE_HOST — pending effacé",
            current + 1,
            target_host + 1,
            elapsed_since_ch,
        )
        state["pending_host"] = None
        return False  # ne pas corriger, accepter la position manuelle

    if current == target_host:
        log.info("Sync confirmée : %s sur hôte %d ✓ — pending effacé", mouse.name, target_host + 1)
        state["pending_host"] = None
        return False

    log.warning(
        "Désync : souris=hôte%d, attendu=hôte%d → correction en cours...",
        current + 1,
        target_host + 1,
    )
    notify(f"Désync corrigée → hôte {target_host + 1}", "SwiGi")
    try:
        send_change_host(
            mouse.transport, DEVICE_NUMBER_DIRECT, mouse.change_host_index, target_host
        )
        log.info("Correction désync → hôte %d ✓", target_host + 1)
        state["pending_host"] = None
    except (TransportError, OSError) as error:
        log.warning(
            "Correction désync échouée : %s — souris reste sur hôte %d, retry au reconnect",
            error,
            current + 1,
        )
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
        log.debug(
            "_send_to_all_mice : mice_list=%s target=%d",
            [f"{mouse.name}(open={mouse.transport.is_open})" for mouse in mice],
            target_host,
        )
        for mouse in list(mice):
            if not mouse.transport.is_open:
                log.debug("[%s] Transport fermé — skip", mouse.name)
                continue
            log.debug("[%s] CHANGE_HOST envoi → hôte %d (transport is_open=True)", mouse.name, target_host)
            try:
                send_change_host(
                    mouse.transport,
                    DEVICE_NUMBER_DIRECT,
                    mouse.change_host_index,
                    target_host,
                )
                log.info("★ CHANGE_HOST → %s → hôte %d ✓", mouse.name, target_host + 1)
                mouse.close()
                log.debug("[%s] Transport fermé après CHANGE_HOST", mouse.name)
            except (TransportError, OSError) as error:
                log.warning("[%s] CHANGE_HOST échoué : %s", mouse.name, error)
                mouse.close()

        # Vider la liste : probe_loop va retrouver les souris avec des handles frais.
        # Sans ce clear(), les DeviceInfo avec transport fermé bloquent la reconnexion
        # (leur Product ID reste dans existing_product_ids → nouvel handle fermé comme "doublon").
        had_mice = len(mice) > 0
        mice.clear()
        state["pending_host"] = (target_host, time.time() + _PENDING_HOST_TTL)
        state["mouse"] = None
        state["mice"] = []
        state["last_change_host_at"] = time.time()
        state["last_change_host_target"] = target_host
        state["last_change_host_had_mice"] = had_mice


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

    log.info(
        "%s Surveillance démarrée (Product ID=0x%04X)", prefix, keyboard.product_id
    )

    while not stop_event.is_set():
        # ── Watchdog ──
        if time.time() - last_response > WATCHDOG_TIMEOUT:
            log.info(
                "%s Watchdog : aucune réponse depuis %ds, reconnexion...",
                prefix,
                int(WATCHDOG_TIMEOUT),
            )
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
                log.debug(
                    "%s Reconnexion en cours (prochaine dans %.1fs)...", prefix, delay
                )

            if new_keyboard:
                keyboard = new_keyboard
                prefix = f"[{keyboard.name}]"
                with state.get("_state_lock") or nullcontext():
                    state["keyboards"][keyboard.product_id] = {
                        "name": keyboard.name,
                        "ok": True,
                    }
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
            # juste avant la déconnexion BT. Sur macOS BT LE, le kernel peut avoir
            # un délai ~10-30ms avant de rendre le dernier paquet disponible après
            # la déconnexion physique. Ne pas sortir immédiatement sur None.
            try:
                none_streak = 0
                for _ in range(24):
                    try:
                        raw_bytes = keyboard.transport.read(timeout=10)
                    except (TransportError, OSError):
                        break
                    if raw_bytes is None or len(raw_bytes) < 4:
                        none_streak += 1
                        if none_streak >= 3:  # 3×10ms = 30ms sans données → buffer épuisé
                            break
                        continue
                    none_streak = 0
                    if (
                        raw_bytes[0] not in MSG_LENGTHS
                        or len(raw_bytes) < MSG_LENGTHS[raw_bytes[0]]
                    ):
                        continue
                    feature_index = raw_bytes[2]
                    software_id = raw_bytes[3] & 0x0F
                    log.debug(
                        "%s DRAIN PKT feat=0x%02X swid=0x%X [%s]",
                        prefix, feature_index, software_id, raw_bytes[:8].hex(),
                    )
                    if (
                        feature_index == keyboard.change_host_index
                        and len(raw_bytes) > 5
                    ):
                        num_hosts = raw_bytes[4] if raw_bytes[4] > 0 else 3
                        target_host = raw_bytes[5]
                        if 0 <= target_host < num_hosts:
                            last_switch_time = time.time()
                            log.info(
                                "%s ★ Touche Easy-Switch %d pressée (drain buffer)",
                                prefix,
                                target_host + 1,
                            )
                            event_queue.put(_SwitchEvent(target_host, keyboard.name))
                        break
            except (TransportError, OSError):
                pass

            switch_triggered = time.time() - last_switch_time <= 3.0
            log.info(
                "%s Déconnecté%s", prefix, " (post-switch)" if switch_triggered else ""
            )
            if not switch_triggered:
                notify(f"{keyboard.name} déconnecté", "Clavier")

            # Détection connexion fantôme : disconnect trop rapide après reconnexion,
            # sans switch → pending_host corrompu par l'hôte transitoire du clavier.
            if not switch_triggered and keyboard_reconnected_at is not None:
                elapsed = time.time() - keyboard_reconnected_at
                if elapsed < _KEYBOARD_PHANTOM_WINDOW:
                    log.warning(
                        "%s Connexion fantôme (%.1fs après reconnexion) → pending_host effacé",
                        prefix,
                        elapsed,
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
                        log.debug(
                            "%s Connexion fantôme (< %.0fs) — réessai",
                            prefix,
                            _KEYBOARD_STABILITY_SECONDS,
                        )
                        new_keyboard.close()
                        new_keyboard = None
                delay = min(delay * 1.5, _KEYBOARD_RECONNECT_MAX_DELAY)
                log.debug(
                    "%s Reconnexion en cours (prochaine dans %.1fs)...", prefix, delay
                )

            if new_keyboard is None:
                continue  # stop_event levé pendant reconnect — la boucle externe sort

            keyboard = new_keyboard
            prefix = f"[{keyboard.name}]"
            with state.get("_state_lock") or nullcontext():
                state["keyboards"][keyboard.product_id] = {
                    "name": keyboard.name,
                    "ok": True,
                }
            _update_keyboard_state(state)
            log.info("%s Reconnexion OK", prefix)
            # N'afficher la notification de reconnexion que si le clavier revient
            # >5s après un switch (retour "inattendu", pas un simple round-trip de switch).
            if time.time() - last_switch_time > 5.0:
                notify(f"{keyboard.name} reconnecté", "Clavier")
            else:
                log.debug("%s Reconnexion post-switch (< 5s) — notification supprimée", prefix)
            last_response = time.time()
            keyboard_reconnected_at = time.time()

            _resync_pending_host_from_keyboard(keyboard, state)
            hunt_trigger.set()
            event_queue.put(_KeyboardReconnected(keyboard.name))
            continue

        # ── Lecture réponses (fenêtre 120ms, reads 10ms) ──
        # timeout=10ms → ~12 reads par fenêtre (vs ~3 avec 25ms).
        # Plus de chances de capturer la notification avant déconnexion BT LE.
        raw_bytes = None
        deadline = time.time() + 0.12
        while time.time() < deadline and not stop_event.is_set():
            try:
                raw_bytes = keyboard.transport.read(timeout=10)
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

            # Dump tous les packets reçus du clavier pour diagnostic
            log.debug(
                "%s PKT report=0x%02X feat=0x%02X fn=0x%02X swid=0x%X [%s]",
                prefix,
                raw_bytes[0],
                feature_index,
                (function_id & 0xF0) >> 4,
                software_id,
                raw_bytes[:8].hex(),
            )

            # Notification CHANGE_HOST — sw_id peut être 0 (spec HID++ 2.0) ou non-zero
            # (certains firmwares Logitech). On accepte tout sw_id pour ce feature.
            if (
                feature_index == keyboard.change_host_index
                and len(raw_bytes) > 5
            ):
                num_hosts = (
                    raw_bytes[4] if raw_bytes[4] > 0 else 3
                )  # fallback 3 si firmware ne renseigne pas
                target_host = raw_bytes[5]
                log.debug(
                    "%s CHANGE_HOST packet : swid=0x%X numHosts=%d newHost=%d",
                    prefix, software_id, num_hosts, target_host,
                )
                if not (0 <= target_host < num_hosts):
                    # Pas une notification de switch — probablement une réponse à notre ping
                    # (le ping répond avec feature=FEATURE_ROOT, pas CHANGE_HOST, mais au cas où)
                    log.debug(
                        "%s CHANGE_HOST hôte invalide %d/%d — probablement réponse ping, ignoré",
                        prefix, target_host, num_hosts,
                    )
                    continue
                # Réponses à nos propres requêtes CHANGE_HOST (send_change_host) ont sw_id=SW_ID (0x0A).
                # Les notifications matérielles (Easy-Switch pressé) ont sw_id=0.
                # On accepte les deux, mais on logge la distinction.
                if software_id != 0:
                    log.debug(
                        "%s CHANGE_HOST sw_id=0x%X (non-zero) → notification firmware acceptée",
                        prefix, software_id,
                    )
                last_switch_time = time.time()
                log.info("─" * 50)
                log.info("%s ★ Touche Easy-Switch %d pressée", prefix, target_host + 1)
                event_queue.put(_SwitchEvent(target_host, keyboard.name))
                break

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
        log.debug(
            "probe: found=%s mice_list=%s pending=%s hunt=%s",
            [f"0x{mouse.product_id:04X}" for mouse in found],
            [f"{mouse.name}(open={mouse.transport.is_open})" for mouse in mice],
            state.get("pending_host"),
            in_hunt,
        )

        # Pass 1 : mises à jour liste (rapide, no I/O) + collecte nouvelles souris
        new_mice = []
        with mouse_lock:
            for new_mouse in found:
                existing = next(
                    (
                        mouse
                        for mouse in mice
                        if mouse.product_id == new_mouse.product_id
                    ),
                    None,
                )
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
            dead = [
                x
                for x in mice
                if not x.transport.is_open and x.product_id not in found_by_product_id
            ]
            for mouse in dead:
                log.info("Souris retirée : %s (plus disponible)", mouse.name)
            mice[:] = [x for x in mice if x not in dead]
            mice_is_empty = len(mice) == 0

        # Pass 1b : confirmation switch — aucune souris visible peu après CHANGE_HOST
        # _send_to_all_mice fait mice.clear() → mice_is_empty=True dès le switch.
        # Si found est aussi vide (souris partie vers l'autre Mac), switch confirmé.
        # Garde : last_change_host_had_mice doit être True — si aucune souris n'était
        # connectée au switch, pending_host doit rester actif pour corriger à la connexion.
        if (
            mice_is_empty
            and not found
            and state.get("pending_host") is not None
            and state.get("last_change_host_had_mice", False)
        ):
            elapsed = time.time() - state.get("last_change_host_at", 0.0)
            if 0.5 <= elapsed <= _CHANGE_HOST_CONFIRM_WINDOW:
                pending = state.get("pending_host")
                if pending is not None:
                    log.info(
                        "Aucune souris visible %.1fs après CHANGE_HOST → switch hôte %d confirmé, pending effacé",
                        elapsed,
                        pending[0] + 1,
                    )
                    state["pending_host"] = None

        # Pass 2 : HID I/O hors du lock (get_current_host peut prendre ~500ms)
        for new_mouse in new_mice:
            log.info(
                "Souris détectée : %s (Product ID=0x%04X)",
                new_mouse.name,
                new_mouse.product_id,
            )
            last_ch = state.get("last_change_host_at", 0.0)
            elapsed = time.time() - last_ch
            pending = state.get("pending_host")
            if pending is not None:
                # pending_host actif = connexion suite à un switch SwiGi en cours
                log.info(
                    "[%s] Connexion post-switch (%.1fs après CHANGE_HOST — hôte cible %d en attente)",
                    new_mouse.name,
                    elapsed,
                    pending[0] + 1,
                )
            elif last_ch == 0.0:
                log.info("[%s] Connexion initiale (premier démarrage)", new_mouse.name)
            elif elapsed < _MANUAL_SWITCH_GRACE:
                last_target = state.get("last_change_host_target")
                target_display = (last_target + 1) if isinstance(last_target, int) else "?"
                log.info(
                    "[%s] Connexion rapide (%.1fs après CHANGE_HOST → hôte %s — switch peut encore être en cours)",
                    new_mouse.name,
                    elapsed,
                    target_display,
                )
            else:
                log.info(
                    "[%s] Connexion manuelle (bouton physique — %.0fs depuis dernier CHANGE_HOST)",
                    new_mouse.name,
                    elapsed,
                )
            notify(f"{new_mouse.name} connectée", "Souris")
            # Race condition guard : _send_to_all_mice peut avoir fermé le transport
            # entre Pass 1 (sous lock) et maintenant (hors lock).
            if not new_mouse.transport.is_open:
                log.debug(
                    "[%s] Transport fermé avant pass 2 (race send_to_all_mice) — skip",
                    new_mouse.name,
                )
                continue
            # Loguer l'hôte actuel de la souris pour diagnostic immédiat.
            try:
                current_host = get_current_host(
                    new_mouse.transport, DEVICE_NUMBER_DIRECT, new_mouse.change_host_index
                )
                if current_host is not None:
                    pending = state.get("pending_host")
                    if pending is not None and current_host != pending[0]:
                        log.info(
                            "[%s] Sur hôte %d → attendu hôte %d",
                            new_mouse.name, current_host + 1, pending[0] + 1,
                        )
                    else:
                        log.info("[%s] Sur hôte %d", new_mouse.name, current_host + 1)
            except (TransportError, OSError):
                pass
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
        log.debug(
            "probe: state mise à jour — mouse=%s mice=%s pending=%s",
            state["mouse"],
            state["mice"],
            state.get("pending_host"),
        )

        if in_hunt and not mice:
            log.debug(
                "Probe (hunt) : souris introuvable, retry dans %ds",
                int(_MOUSE_HUNT_INTERVAL),
            )

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
    state["keyboards"] = {
        keyboard.product_id: {"name": keyboard.name, "ok": True}
        for keyboard in keyboards
    }
    state["mouse"] = mice[0].name if mice else None
    state["mice"] = [mouse.name for mouse in mice]
    state.setdefault("pending_host", None)
    state.setdefault("switches", 0)
    state.setdefault("last_change_host_at", 0.0)
    state.setdefault("last_change_host_target", None)
    state.setdefault("last_change_host_had_mice", False)

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
        log.info(
            "Thread clavier démarré : %s (Product ID=0x%04X)",
            keyboard.name,
            keyboard.product_id,
        )

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
            log.info(
                "★ Touche Easy-Switch %d → synchronisation clavier+souris",
                event.target_host + 1,
            )

            # Vérifier si le suivi de la souris est activé
            with _prefs_lock:
                mouse_follow = prefs.get("mouse_follow", True)

            if mouse_follow:
                _send_to_all_mice(mice_list, event.target_host, state, mouse_lock)
                if not state.get("last_change_host_had_mice"):
                    log.warning(
                        "Aucune souris disponible au moment du switch → "
                        "en attente de connexion souris sur hôte %d",
                        event.target_host + 1,
                    )
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
