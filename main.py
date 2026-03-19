import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Scripts'))

import json
import threading
import time
import shutil
import subprocess
import multiprocessing
import atexit
import logging

import Database
import Analyze_proxy
from Analyze_proxy import FridaCrashError
import adb_utils
import config  # Configuration DEV/PROD
from har_flow_extractor import extract_flow, find_anchor_in_flow
from har_script_generator import generate_replay_script, run_flow_analysis
from live_view import LiveViewServer

from emulator_utils import (
    is_device_online, wait_for_boot, wait_for_tcp_port,
    ensure_root_environment, wait_for_android_ready,
)
from pipeline_utils import (
    kill_process_on_port, cleanup_orphan_processes,
    check_host_internet_connectivity, restore_host_connectivity,
    install_from_local, analyze_app_with_timeout,
    PackageServiceDeadError,
)

logger = logging.getLogger(__name__)

# ==========================================================
# ============= MAPPING RÉSULTATS → GRAFANA ================
# ==========================================================

_RESULT_LABELS = {
    # Résultats positifs (HAR capturé)
    "END_EMAIL_UNIQUE_NO_SUBMIT_POSITIVE":  "HAR - Email unique (Enter)",
    "END_EMAIL_UNIQUE_SUBMIT_POSITIVE":     "HAR - Email unique (bouton)",
    "END_EMAIL_MDP_OK_POSITIVE":            "HAR - Email + MDP",
    "END_EMAIL_OK_POSITIVE":                "HAR - Register",
    # Résultats négatifs attendus
    "END_NO_INFO_REGISTER":                 "Email non reconnu",
    "NO_LOGIN":                             "Pas de login email",
    "NO_REGISTER":                          "Pas de page register",
    "FAILED_GO_TO_LOGIN":                   "Login introuvable (max tentatives)",
    # Play Store requis
    "PLAY_STORE_REQUIRED":                  "Erreur : Play Store requis",
    # HAR manquant malgré résultat positif
    "ERROR_HAR_NOT_CAPTURED":               "Erreur: HAR non capturé",
    # Arrêts volontaires
    "ERROR_CHROME":                         "Stop: Chrome en premier plan",
    "APP_QUIT":                             "Stop: App quittée",
    "INSTALL_FAILED":                       "Installation échouée",
    # Erreurs IA
    "UNKOWN_ENDING":                        "Erreur: Inattendue",
    "ERROR_EMAIL_CHECK_VALUE":              "Erreur: IA (valeur inattendue)",
    "ERROR_EMAIL_REGISTER_PAGE":            "Erreur: IA (valeur inattendue)",
    "ERROR_EMAIL_REGISTER_CHECK_VALUE":     "Erreur: IA (valeur inattendue)",
    "ERROR_REGISTER_NO_EMAIL":              "Erreur: IA (valeur inattendue)",
    "ERROR_REGISTER_PAGE":                  "Erreur: IA (valeur inattendue)",
    "ERROR_REGISTER_EMAIL_PAGE_VALUE":      "Erreur: IA (valeur inattendue)",
    # Crash émulateur (app répétée)
    "FRIDA_ERROR_EMULATOR_CRASH":           "Crash: Émulateur",
    # Crashes Frida
    "ERROR_PROCESS_TERMINATED":             "App terminée",
    "FRIDA_ERROR_TRACE_BPT_TRAP":           "Crash: Bad access",
    "FRIDA_ERROR_BAD_ACCESS":               "Crash: Bad access",
    "FRIDA_ERROR_SEGFAULT":                 "Crash: Segfault",
    "FRIDA_ERROR_SIGABRT":                  "Crash: Abort",
    "FRIDA_ERROR_SIGKILL":                  "Crash: Killed",
    "FRIDA_ERROR_FRIDA_SERVER_NOT_RUNNING": "Crash: Démarrage Frida",
    "FRIDA_ERROR_UNSATISFIED_LINK":         "Crash: Lib native",
    "FRIDA_ERROR_FATAL_EXCEPTION":          "Crash: Exception Java",
    "FRIDA_ERROR_FRIDA_PYTHON_ERROR":       "Crash: Erreur Frida interne",
    "FRIDA_ERROR_DLOPEN_FAILED":            "Crash: Lib native",
    "FRIDA_ERROR_STARTUP_ERROR":            "Crash: Démarrage Frida",
    "FRIDA_ERROR_APP_CRASH":               "Crash: Bad access",
}


def _get_explicit_label(raw: str) -> str:
    """Convertit un code résultat brut en libellé lisible pour Grafana."""
    if not raw:
        return "Erreur: Inattendue"
    if raw in _RESULT_LABELS:
        return _RESULT_LABELS[raw]
    if raw.startswith("TIMEOUT"):
        return "Timeout"
    if raw.startswith("ADB_ERROR"):
        return "Erreur: ADB"
    if raw.startswith("FRIDA_ERROR_"):
        return "Crash: Erreur Frida interne"
    if raw.startswith("UNEXPECTED"):
        return "Erreur: Inattendue"
    return "Erreur: Inattendue"


try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil non disponible, nettoyage des processus orphelins désactivé")


# ==========================================================
# ===================== CONFIGURATION ======================
# ==========================================================
AVD_MAPPING      = config.AVD_MAPPING
EMULATOR_BINARY  = config.EMULATOR_BINARY
ADB_BINARY       = config.ADB_BINARY
EMULATOR_LAUNCH_OPTS = config.EMULATOR_LAUNCH_OPTS
WATCHDOG_INTERVAL    = config.WATCHDOG_INTERVAL
STABILIZATION_DELAY  = config.STABILIZATION_DELAY
POPUP_CHECK_DELAY    = config.POPUP_CHECK_DELAY
STARTUP_DELAY        = config.STARTUP_DELAY_BETWEEN_EMULATORS
PACKAGES_BASE_PATH   = config.PACKAGES_BASE_PATH

# Variables d'état globales
_crash_counts   = {}  # package_id -> nb de crashes émulateur consécutifs
EMULATOR_STATES = {}  # serial -> "RUNNING" | "STARTING" | "OFFLINE"
EMULATOR_LOCKS  = {}  # serial -> threading.Lock()
STOP_EVENT      = threading.Event()
ADB_LOCK        = threading.Lock()
MAGISK_CONFIG   = {}  # serial -> proxy_port
WORKER_THREADS  = []


# ==========================================================
# ===================== NETTOYAGE ==========================
# ==========================================================

def cleanup_on_exit():
    """Libère tous les comptes en cours d'utilisation à la sortie."""
    logger.info("Nettoyage de sortie...")
    try:
        Database.release_all_accounts()
    except Exception as e:
        logger.warning(f"Erreur lors du nettoyage: {e}")


# ==========================================================
# ===================== EMULATEUR ==========================
# ==========================================================

def restart_emulator(serial, avd_mapping, emulator_path=EMULATOR_BINARY):
    """Redémarrage complet + attente Android ready + configuration ROOT."""
    lock = EMULATOR_LOCKS[serial]
    if not lock.acquire(blocking=False):
        logger.info(f"[{serial}] déjà en cours de démarrage")
        return False

    try:
        EMULATOR_STATES[serial] = "STARTING"
        Database.update_emulator_status(serial, "STARTING")

        info = avd_mapping.get(serial)
        if not info:
            logger.error(f"Pas de mapping pour {serial}")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        avd_name = info["avd"]
        port     = serial.split("-")[-1]
        port_int = int(port)

        logger.info(f"[{serial}] 🔄 Redémarrage en cours...")

        # Kill si accroché
        try:
            subprocess.run(
                f"{ADB_BINARY} -s {serial} emu kill",
                shell=True, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
        except Exception:
            pass

        # Lancer l'émulateur
        cmd = f"{emulator_path} -avd {avd_name} -port {port} {EMULATOR_LAUNCH_OPTS}"
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Attente ADB
        subprocess.run(f"{ADB_BINARY} -s {serial} wait-for-device", shell=True, timeout=120)

        # Attente boot complet
        if not wait_for_boot(serial):
            logger.error(f"[{serial}] Boot timeout")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        if not wait_for_android_ready(serial):
            logger.error(f"[{serial}] Android pas prêt")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        # Play Store natif — GMS et com.android.vending sont toujours présents

        # Attente port TCP ouvert
        if not wait_for_tcp_port(port=port_int, timeout=60):
            logger.error(f"[{serial}] Port TCP {port_int} indisponible")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        time.sleep(5)

        # Configuration root via Magisk su (adb root indisponible sur images Play Store)
        if not ensure_root_environment(serial):
            logger.error(f"[{serial}] impossible de passer en root")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        # Cert mitmproxy installé de façon permanente via module Magisk → vérification seulement
        r_cert = subprocess.run(
            f"{ADB_BINARY} -s {serial} shell 'ls /system/etc/security/cacerts/ | grep c8750f0d'",
            shell=True, timeout=10, capture_output=True, text=True
        )
        if r_cert.returncode != 0 or "c8750f0d" not in r_cert.stdout:
            logger.warning(f"[{serial}] Cert mitmproxy ABSENT des certs système — réinstaller via module Magisk")

        # Nettoyage proxy résiduel via su (settings global nécessite root sur Play Store)
        for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell 'su -c \"settings delete global {key}\"'",
                shell=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        time.sleep(STABILIZATION_DELAY)
        time.sleep(POPUP_CHECK_DELAY)

        with ADB_LOCK:
            adb_utils.dismiss_not_responding_popup(serial)
            adb_utils.uninstall_all_third_party_packages(serial)

        if not is_device_online(serial):
            logger.error(f"[{serial}] offline après stabilisation")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        EMULATOR_STATES[serial] = "RUNNING"
        Database.update_emulator_status(serial, "RUNNING")
        logger.info(f"[{serial}] 🟢 prêt")
        return True

    finally:
        lock.release()


# ==========================================================
# ===================== WATCHDOG ===========================
# ==========================================================

def emulator_watchdog(avd_mapping, interval=15):
    """Surveille tous les émulateurs ROOT et les redémarre si nécessaire."""
    while not STOP_EVENT.is_set():
        for serial in avd_mapping:
            state = EMULATOR_STATES.get(serial, "OFFLINE")
            if state == "STARTING":
                continue
            online = is_device_online(serial)
            if not online or state == "OFFLINE":
                if state == "RUNNING":
                    logger.warning(f"[{serial}] a crashé (était RUNNING), redémarrage...")
                elif not online:
                    logger.warning(f"[{serial}] offline, tentative de redémarrage")
                else:
                    logger.warning(f"[{serial}] état OFFLINE (service Android mort), redémarrage forcé...")
                restart_emulator(serial, avd_mapping)
        time.sleep(interval)


# ==========================================================
# ===================== UTILITAIRES ========================
# ==========================================================

def create_health_check(serial):
    """
    Crée une fonction de vérification de santé pour un émulateur.
    Lève une exception si l'émulateur est offline ou arrêt global demandé.
    """
    def health_check():
        if STOP_EVENT.is_set():
            raise InterruptedError(f"[{serial}] Arrêt global demandé")
        if not is_device_online(serial):
            raise RuntimeError(f"[{serial}] Émulateur offline (health check)")
        if EMULATOR_STATES.get(serial) != "RUNNING":
            raise RuntimeError(f"[{serial}] État émulateur: {EMULATOR_STATES.get(serial)}")
        return True
    return health_check


def _read_har_file(har_path: str, device_id: str):
    """
    Lit le fichier HAR depuis le disque et retourne son contenu parsé.
    Supprime le fichier après lecture (succès ou erreur).

    Returns:
        dict si des entrées réseau ont été capturées, None sinon
    """
    if not os.path.exists(har_path) or os.path.getsize(har_path) == 0:
        logger.info(f"[{device_id}] ⚫ 0 logs HAR générés.")
        return None

    try:
        with open(har_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            logger.info(f"[{device_id}] ⚫ 0 logs HAR générés.")
            return None

        har_content = json.loads(content)
        entries_count = len(har_content.get("log", {}).get("entries", []))
        if entries_count > 0:
            logger.info(f"[{device_id}] 🟢 {entries_count} logs HAR générés.")
            return har_content
        else:
            logger.info(f"[{device_id}] ⚫ 0 logs HAR générés.")
            return None

    except json.JSONDecodeError as e:
        logger.warning(f"[{device_id}] Fichier HAR corrompu: {e}")
        return None
    except Exception as e:
        logger.warning(f"[{device_id}] Erreur lecture HAR: {e}")
        return None
    finally:
        try:
            os.remove(har_path)
        except Exception:
            pass


def _reset_network(serial: str):
    """Reset réseau via mode avion ON/OFF."""
    airplane_on = False
    try:
        subprocess.run(
            f"{ADB_BINARY} -s {serial} shell settings put global airplane_mode_on 1",
            shell=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.run(
            f'{ADB_BINARY} -s {serial} shell am broadcast -a android.intent.action.AIRPLANE_MODE --ez state true',
            shell=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        airplane_on = True
        time.sleep(2)
        subprocess.run(
            f"{ADB_BINARY} -s {serial} shell settings put global airplane_mode_on 0",
            shell=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.run(
            f'{ADB_BINARY} -s {serial} shell am broadcast -a android.intent.action.AIRPLANE_MODE --ez state false',
            shell=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        airplane_on = False
        time.sleep(5)
    except subprocess.TimeoutExpired:
        logger.warning(f"[{serial}] Timeout sur reset réseau")
    finally:
        if airplane_on:
            try:
                subprocess.run(
                    f"{ADB_BINARY} -s {serial} shell settings put global airplane_mode_on 0",
                    shell=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                subprocess.run(
                    f'{ADB_BINARY} -s {serial} shell am broadcast -a android.intent.action.AIRPLANE_MODE --ez state false',
                    shell=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                time.sleep(5)
            except Exception as e:
                logger.error(f"[{serial}] Impossible de désactiver le mode avion: {e}")


def _cleanup_after_analysis(serial: str, package_id: str, proxy_port: int):
    """Nettoyage systématique post-analyse : proxy, réseau, désinstallation."""
    cleanup_orphan_processes(proxy_port, serial)

    if not is_device_online(serial):
        logger.warning(f"[{serial}] Émulateur offline, skip nettoyage")
        return

    # Suppression proxy via su (settings global nécessite root sur Play Store)
    try:
        for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell 'su -c \"settings delete global {key}\"'",
                shell=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        pass  # Proxy supprimé (log en warning si timeout ci-dessus)
    except subprocess.TimeoutExpired:
        logger.warning(f"[{serial}] Timeout suppression proxy")

    # Reset réseau
    _reset_network(serial)

    # Désinstallation
    try:
        subprocess.run(
            f"{ADB_BINARY} -s {serial} uninstall {package_id}",
            shell=True, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"[{serial}] Timeout désinstallation {package_id}")

    # Fermeture des apps résiduelles + vérification mode avion
    adb_utils.close_all_apps(serial)
    if adb_utils.disable_airplane_mode_if_on(serial):
        logger.warning(f"[{serial}] Mode avion était actif — désactivé avant prochaine analyse")

    # Vérification connectivité hôte
    if not check_host_internet_connectivity():
        logger.warning(f"[{serial}] Connectivité hôte perdue après nettoyage!")
        restore_host_connectivity(serial, proxy_port)

    # Suppression du dossier APK
    apk_folder = os.path.join(PACKAGES_BASE_PATH, package_id)
    if os.path.isdir(apk_folder):
        shutil.rmtree(apk_folder, ignore_errors=True)

    logger.info(f"[{serial}] ✅ {package_id} - désinstallation et reset validé")


# ==========================================================
# ==================== MAGISK WORKER =======================
# ==========================================================

def magisk_worker(serial):
    """
    Worker MAGISK (Play Store + Magisk + Play Integrity Fix) :
    récupère les packages depuis packages_full_pipeline
    (frida_analyze IS NULL), installe depuis le dossier local, analyse,
    stocke le résultat et marque comme terminé.
    S'arrête quand il n'y a plus de packages à traiter.
    """
    proxy_port = MAGISK_CONFIG[serial]

    logger.info(f"[MAGISK] Worker démarré pour {serial}")

    # Attente que l'émulateur soit prêt
    while not is_device_online(serial) or EMULATOR_STATES.get(serial) != "RUNNING":
        logger.info(f"[{serial}] attente démarrage (state={EMULATOR_STATES.get(serial)})...")
        time.sleep(2)

    # Nettoyage proxy initial via su (settings global nécessite root sur Play Store)
    logger.info(f"[{serial}] Nettoyage proxy initial...")
    try:
        for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell 'su -c \"settings delete global {key}\"'",
                shell=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception as e:
        logger.warning(f"[{serial}] Erreur nettoyage proxy initial: {e}")

    while not STOP_EVENT.is_set():
        # --- Vérification état émulateur ---
        if not is_device_online(serial) or EMULATOR_STATES.get(serial) != "RUNNING":
            logger.warning(f"[{serial}] Émulateur non disponible, attente...")
            time.sleep(5)
            continue

        # --- Claim du prochain package ---
        package_id = Database.get_next_package_for_analysis()
        if package_id is None:
            logger.info(f"[{serial}] Plus aucun package à analyser, attente 10 min...")
            STOP_EVENT.wait(timeout=600)
            continue

        logger.info(f"[{serial}] 📦 {package_id}")
        analysis_completed = False

        # Rotation capture_all.har → capture_all_previous.har avant la nouvelle analyse
        all_har = os.path.join("temp", serial, "capture_all.har")
        all_har_prev = os.path.join("temp", serial, "capture_all_previous.har")
        if os.path.exists(all_har):
            try:
                shutil.move(all_har, all_har_prev)
                logger.info(f"[{serial}] capture_all.har sauvegardé → capture_all_previous.har")
            except Exception as e:
                logger.warning(f"[{serial}] Impossible de sauvegarder capture_all.har: {e}")

        # Suppression des fichiers HAR/jsonl résiduels
        for fname in ("capture.har", "capture.jsonl"):
            fpath = os.path.join("temp", serial, fname)
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    logger.info(f"[{serial}] Fichier résiduel supprimé: {fname}")
                except Exception as e:
                    logger.warning(f"[{serial}] Impossible de supprimer {fname}: {e}")

        try:
            # --- Vérification état avant traitement ---
            if not is_device_online(serial) or EMULATOR_STATES.get(serial) != "RUNNING":
                logger.error(f"[{serial}] offline avant traitement de {package_id}, remise en attente")
                Database.reset_package_to_pending(package_id)
                time.sleep(5)
                continue

            # --- Vérification connectivité hôte ---
            if not check_host_internet_connectivity():
                logger.warning(f"[{serial}] Connectivité hôte perdue avant analyse!")
                if not restore_host_connectivity(serial, proxy_port):
                    logger.error(f"[{serial}] Connectivité hôte KO, remise en attente de {package_id}")
                    Database.reset_package_to_pending(package_id)
                    time.sleep(30)
                    continue

            # Nettoyage processus orphelins
            cleanup_orphan_processes(proxy_port, serial)

            # Nettoyage proxy pré-analyse via su
            try:
                for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
                    subprocess.run(
                        f"{ADB_BINARY} -s {serial} shell 'su -c \"settings delete global {key}\"'",
                        shell=True, timeout=10,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
            except subprocess.TimeoutExpired:
                logger.warning(f"[{serial}] Timeout nettoyage proxy pré-analyse")

            # --- Installation depuis le dossier local ---
            if not install_from_local(package_id, PACKAGES_BASE_PATH, serial):
                logger.error(f"[{serial}] Installation échouée pour {package_id}")
                Database.set_frida_error(package_id, "INSTALL_FAILED",
                                         explicit_result=_get_explicit_label("INSTALL_FAILED"))
                analysis_completed = True
                Database.increment_emulator_error(serial)
                continue

            # --- Analyse Frida + mitmproxy ---
            health_check = create_health_check(serial)
            resp = analyze_app_with_timeout(
                package_id, serial, proxy_port,
                timeout=360, health_check=health_check
            )
            result_emoji = "❌" if resp and resp.startswith(("ERROR_", "TIMEOUT", "UNKOWN_")) else "✅"
            logger.info(f"[{serial}] {result_emoji} Analyse terminée : {package_id} → {resp}")

            # --- Lecture du HAR (laissé par analyze_app) ---
            har_path = os.path.join("temp", serial, "capture.har")
            har_data = _read_har_file(har_path, serial)

            # --- Détection HAR manquant sur résultat positif ---
            _HAR_EXPECTED = ("END_EMAIL_UNIQUE_SUBMIT_POSITIVE", "END_EMAIL_MDP_OK_POSITIVE")
            if resp in _HAR_EXPECTED and har_data is None:
                logger.warning(f"[{serial}] HAR non capturé pour {package_id} (résultat: {resp})")
                capture_all_src = os.path.join("temp", serial, "capture_all.har")
                if os.path.exists(capture_all_src):
                    dest_dir = os.path.join("results", package_id)
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.copy2(capture_all_src, os.path.join(dest_dir, "capture_all.har"))
                    logger.info(f"[{serial}] capture_all.har copié → results/{package_id}/")
                else:
                    logger.warning(f"[{serial}] capture_all.har introuvable pour {package_id}")
                resp = "ERROR_HAR_NOT_CAPTURED"

            # --- Enregistrement du résultat ---
            if resp == "ERROR_NO_INTERNET":
                logger.warning(f"[{serial}] Internet KO détecté → redémarrage émulateur + remise en attente de {package_id}")
                Database.reset_package_to_pending(package_id)
                analysis_completed = True
                restart_emulator(serial, AVD_MAPPING)
                continue

            explicit = _get_explicit_label(resp or "")
            if resp and resp.startswith(("ERROR_", "TIMEOUT", "UNKOWN_")):
                Database.set_frida_error(package_id, resp, explicit_result=explicit)
            else:
                Database.complete_package_analysis(package_id, result=har_data, explicit_result=explicit)

            # --- Flow analysis : détection email enumeration (si HAR capturé) ---
            if har_data is not None:
                capture_all_path = os.path.join("temp", serial, "capture_all.har")
                if os.path.exists(capture_all_path):
                    try:
                        flow, anchor_idx = extract_flow(capture_all_path, verbose=False)
                        if flow:
                            generate_replay_script(
                                package_id, flow, anchor_idx,
                                output_dir=os.path.join("results_script"),
                            )
                            if run_flow_analysis(flow, anchor_idx, verbose=False):
                                Database.set_request_auto(package_id)
                                logger.info(f"[{serial}] 🔍 request_auto=TRUE → {package_id}")
                    except Exception as e:
                        logger.warning(f"[{serial}] Flow analysis échouée pour {package_id}: {e}")
                else:
                    logger.debug(f"[{serial}] capture_all.har absent, flow analysis ignorée")

            analysis_completed = True
            Database.increment_emulator_finished(serial)

        except PackageServiceDeadError as e:
            logger.error(f"[{serial}] {e} — marquage OFFLINE pour redémarrage watchdog")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            Database.reset_package_to_pending(package_id)
            time.sleep(5)

        except FridaCrashError as e:
            error_code = (
                "ERROR_PROCESS_TERMINATED"
                if e.error_type == "PROCESS_TERMINATED"
                else f"FRIDA_ERROR_{e.error_type}"
            )
            logger.error(f"[{serial}] Crash Frida pour {package_id}: {error_code}")
            Database.set_frida_error(package_id, error_code,
                                     explicit_result=_get_explicit_label(error_code))
            analysis_completed = True
            Database.increment_emulator_error(serial)

        except (RuntimeError, InterruptedError) as e:
            logger.warning(f"[{serial}] Health check échoué pour {package_id}: {e}")
            if not analysis_completed:
                MAX_EMULATOR_CRASHES = 2
                count = _crash_counts.get(package_id, 0) + 1
                _crash_counts[package_id] = count
                if count >= MAX_EMULATOR_CRASHES:
                    logger.error(
                        f"[{serial}] {package_id} a crashé l'émulateur {count}x "
                        f"→ marqué EMULATOR_CRASH"
                    )
                    Database.set_frida_error(
                        package_id, "FRIDA_ERROR_EMULATOR_CRASH",
                        explicit_result=_get_explicit_label("FRIDA_ERROR_EMULATOR_CRASH")
                    )
                    _crash_counts.pop(package_id, None)
                    analysis_completed = True
                    Database.increment_emulator_error(serial)
                else:
                    logger.warning(
                        f"[{serial}] {package_id} → remise en attente "
                        f"(crash émulateur {count}/{MAX_EMULATOR_CRASHES})"
                    )
                    Database.reset_package_to_pending(package_id)
            time.sleep(2)

        except subprocess.CalledProcessError as e:
            msg = f"ADB_ERROR: {e.returncode}"
            logger.error(f"[{serial}] Erreur ADB pour {package_id}: {e}")
            Database.set_frida_error(package_id, msg,
                                     explicit_result=_get_explicit_label(msg))
            analysis_completed = True
            Database.increment_emulator_error(serial)

        except Exception as e:
            msg = f"UNEXPECTED: {type(e).__name__}: {str(e)[:200]}"
            logger.exception(f"[{serial}] Erreur inattendue pour {package_id}")
            if not analysis_completed:
                Database.set_frida_error(package_id, msg,
                                         explicit_result=_get_explicit_label(msg))
                analysis_completed = True
            Database.increment_emulator_error(serial)

        finally:
            if package_id:
                Database.touch_frida_analyze_at(package_id)
                _cleanup_after_analysis(serial, package_id, proxy_port)

    logger.info(f"[{serial}] Worker ROOT terminé")


# ==========================================================
# ===================== MAIN ===============================
# ==========================================================

if __name__ == "__main__":
    multiprocessing.freeze_support()

    config.print_config()
    config.setup_logging()

    # Enregistrer le handler de nettoyage uniquement dans le processus principal
    atexit.register(cleanup_on_exit)

    # --- Récupération des émulateurs MAGISK (Play Store + Magisk) ---
    MAGISK_EMULATORS = [s for s, i in AVD_MAPPING.items() if i["type"] == "MAGISK"]
    ALL_DEVICES = MAGISK_EMULATORS

    # --- Log dédié par émulateur ---
    for serial in ALL_DEVICES:
        config.setup_emulator_logger(serial)

    if not MAGISK_EMULATORS:
        logger.error("Aucun émulateur MAGISK configuré dans AVD_MAPPING. Arrêt.")
        sys.exit(1)

    logger.info(f"Émulateurs MAGISK configurés: {MAGISK_EMULATORS}")
    logger.info(f"Dossier APKs: {PACKAGES_BASE_PATH}")

    # --- Remise à zéro des packages bloqués (crash précédent) ---
    try:
        with Database.get_cursor() as cur:
            cur.execute(
                "UPDATE packages_full_pipeline SET frida_analyze = NULL WHERE frida_analyze = FALSE;"
            )
            count = cur.rowcount
        if count:
            logger.info(f"{count} package(s) bloqué(s) (frida_analyze=FALSE) remis en attente")
    except Exception as e:
        logger.warning(f"Impossible de remettre à zéro les packages bloqués: {e}")

    # --- Initialisation des états, locks et DB ---
    Database.reset_emulators()
    for serial in ALL_DEVICES:
        EMULATOR_STATES[serial] = "OFFLINE"
        EMULATOR_LOCKS[serial]  = threading.Lock()
        Database.add_emulator(serial, "Magisk", status="OFFLINE")

    # --- Ports proxy MAGISK ---
    for idx, serial in enumerate(MAGISK_EMULATORS):
        MAGISK_CONFIG[serial] = config.ROOT_PORT_START + idx

    # --- Dossiers temporaires ---
    for serial in ALL_DEVICES:
        shutil.rmtree(os.path.join("temp", serial), ignore_errors=True)
        os.makedirs(os.path.join("temp", serial), exist_ok=True)

    # --- Live view ---
    live_view = LiveViewServer(devices=ALL_DEVICES)
    live_view.start()

    # --- Démarrage du daemon ADB ---
    logger.info("Vérification du daemon ADB...")
    try:
        subprocess.run(
            f"{ADB_BINARY} kill-server", shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
        time.sleep(1)
        subprocess.run(
            f"{ADB_BINARY} start-server", shell=True, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10
        )
        logger.info("Daemon ADB prêt")
    except Exception as e:
        logger.warning(f"Erreur daemon ADB: {e} — tentative de continuer quand même...")

    # --- Démarrage des émulateurs en parallèle ---
    logger.info("Démarrage des émulateurs MAGISK (Play Store) en parallèle...")

    def start_emulator(serial):
        success = restart_emulator(serial, AVD_MAPPING)
        if not success:
            logger.error(f"Impossible de démarrer {serial}")
            EMULATOR_STATES[serial] = "OFFLINE"

    startup_threads = []
    for idx, serial in enumerate(ALL_DEVICES):
        t = threading.Thread(target=start_emulator, args=(serial,), daemon=False)
        t.start()
        startup_threads.append(t)
        if idx < len(ALL_DEVICES) - 1:
            time.sleep(STARTUP_DELAY)

    for t in startup_threads:
        t.join()

    logger.info("Démarrage initial terminé")

    # --- Watchdog (après démarrage initial) ---
    watchdog_thread = threading.Thread(
        target=emulator_watchdog,
        args=(AVD_MAPPING,),
        daemon=True
    )
    watchdog_thread.start()
    logger.info("Watchdog des émulateurs actif")

    # --- Lancement des workers MAGISK ---
    for serial in MAGISK_EMULATORS:
        while EMULATOR_STATES.get(serial) != "RUNNING":
            time.sleep(1)
        t = threading.Thread(target=magisk_worker, args=(serial,), name=f"MAGISK-{serial}")
        t.start()
        WORKER_THREADS.append(t)
        logger.info(f"[MAGISK] Worker lancé pour {serial}")

    logger.info("🚀 PIPELINE ACTIF — En attente de la fin des analyses...")

    # --- Boucle principale : attente de la fin de tous les workers ---
    try:
        for t in WORKER_THREADS:
            while t.is_alive():
                t.join(timeout=1)
                # Permet de détecter le Ctrl+C même pendant le join
    except KeyboardInterrupt:
        logger.info("Arrêt demandé (Ctrl+C)...")
        STOP_EVENT.set()

        logger.info("Attente de la fin des workers...")
        for t in WORKER_THREADS:
            t.join(timeout=30)
            if t.is_alive():
                logger.warning(f"Thread {t.name} ne répond pas, abandon")

    logger.info("Arrêt des émulateurs...")
    for serial in ALL_DEVICES:
        try:
            subprocess.run(
                f"{ADB_BINARY} -s {serial} emu kill",
                shell=True, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
        except Exception:
            pass

    logger.info("Terminé — plus aucun package à analyser.")
