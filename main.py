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
# ============= RESULTS → GRAFANA LABEL MAPPING ============
# ==========================================================

_RESULT_LABELS = {
    # Positive results (HAR captured)
    "END_EMAIL_UNIQUE_NO_SUBMIT_POSITIVE":  "HAR - Email only (Enter)",
    "END_EMAIL_UNIQUE_SUBMIT_POSITIVE":     "HAR - Email only (button)",
    "END_EMAIL_MDP_OK_POSITIVE":            "HAR - Email + Password",
    "END_EMAIL_OK_POSITIVE":                "HAR - Register",
    # Expected negative results
    "END_NO_INFO_REGISTER":                 "Email not recognized",
    "NO_LOGIN":                             "No email login",
    "NO_REGISTER":                          "No register page",
    "FAILED_GO_TO_LOGIN":                   "Login not found (max attempts)",
    # Play Store required
    "PLAY_STORE_REQUIRED":                  "Error: Play Store required",
    # Voluntary stops
    "ERROR_CHROME":                         "Stop: Chrome in foreground",
    "APP_QUIT":                             "Stop: App quit",
    "INSTALL_FAILED":                       "Installation failed",
    # AI errors
    "UNKOWN_ENDING":                        "Error: Unexpected",
    "ERROR_EMAIL_CHECK_VALUE":              "Error: AI (unexpected value)",
    "ERROR_EMAIL_REGISTER_PAGE":            "Error: AI (unexpected value)",
    "ERROR_EMAIL_REGISTER_CHECK_VALUE":     "Error: AI (unexpected value)",
    "ERROR_REGISTER_NO_EMAIL":              "Error: AI (unexpected value)",
    "ERROR_REGISTER_PAGE":                  "Error: AI (unexpected value)",
    "ERROR_REGISTER_EMAIL_PAGE_VALUE":      "Error: AI (unexpected value)",
    # Emulator crash (repeated app)
    "FRIDA_ERROR_EMULATOR_CRASH":           "Crash: Emulator",
    # Frida crashes
    "ERROR_PROCESS_TERMINATED":             "App terminated",
    "FRIDA_ERROR_TRACE_BPT_TRAP":           "Crash: Bad access",
    "FRIDA_ERROR_BAD_ACCESS":               "Crash: Bad access",
    "FRIDA_ERROR_SEGFAULT":                 "Crash: Segfault",
    "FRIDA_ERROR_SIGABRT":                  "Crash: Abort",
    "FRIDA_ERROR_SIGKILL":                  "Crash: Killed",
    "FRIDA_ERROR_FRIDA_SERVER_NOT_RUNNING": "Crash: Frida startup",
    "FRIDA_ERROR_UNSATISFIED_LINK":         "Crash: Native lib",
    "FRIDA_ERROR_FATAL_EXCEPTION":          "Crash: Java exception",
    "FRIDA_ERROR_FRIDA_PYTHON_ERROR":       "Crash: Internal Frida error",
    "FRIDA_ERROR_DLOPEN_FAILED":            "Crash: Native lib",
    "FRIDA_ERROR_STARTUP_ERROR":            "Crash: Frida startup",
    "FRIDA_ERROR_APP_CRASH":               "Crash: Bad access",
}


def _get_explicit_label(raw: str) -> str:
    """Converts a raw result code to a human-readable label for Grafana."""
    if not raw:
        return "Error: Unexpected"
    if raw in _RESULT_LABELS:
        return _RESULT_LABELS[raw]
    if raw.startswith("TIMEOUT"):
        return "Timeout"
    if raw.startswith("ADB_ERROR"):
        return "Error: ADB"
    if raw.startswith("FRIDA_ERROR_"):
        return "Crash: Internal Frida error"
    if raw.startswith("UNEXPECTED"):
        return "Error: Unexpected"
    return "Error: Unexpected"


try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not available, orphan process cleanup disabled")


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

# Global state variables
_crash_counts   = {}  # package_id -> number of consecutive emulator crashes
EMULATOR_STATES = {}  # serial -> "RUNNING" | "STARTING" | "OFFLINE"
EMULATOR_LOCKS  = {}  # serial -> threading.Lock()
STOP_EVENT      = threading.Event()
ADB_LOCK        = threading.Lock()
ROOT_CONFIG     = {}  # serial -> proxy_port
WORKER_THREADS  = []


# ==========================================================
# ===================== CLEANUP ============================
# ==========================================================

def cleanup_on_exit():
    """Releases all accounts currently in use at exit."""
    logger.info("Exit cleanup...")
    try:
        Database.release_all_accounts()
    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")


# ==========================================================
# ===================== EMULATOR ===========================
# ==========================================================

def restart_emulator(serial, avd_mapping, emulator_path=EMULATOR_BINARY):
    """Full restart + wait for Android ready + ROOT configuration."""
    lock = EMULATOR_LOCKS[serial]
    if not lock.acquire(blocking=False):
        logger.info(f"[{serial}] already starting")
        return False

    try:
        EMULATOR_STATES[serial] = "STARTING"
        Database.update_emulator_status(serial, "STARTING")

        info = avd_mapping.get(serial)
        if not info:
            logger.error(f"No mapping for {serial}")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        avd_name = info["avd"]
        port     = serial.split("-")[-1]
        port_int = int(port)

        logger.info(f"[{serial}] Restarting (ROOT)")

        # Kill if stuck
        try:
            subprocess.run(
                f"{ADB_BINARY} -s {serial} emu kill",
                shell=True, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
        except Exception:
            pass

        # Launch the emulator
        cmd = f"{emulator_path} -avd {avd_name} -port {port} {EMULATOR_LAUNCH_OPTS}"
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Attente ADB
        logger.info(f"[{serial}] wait-for-device...")
        subprocess.run(f"{ADB_BINARY} -s {serial} wait-for-device", shell=True, timeout=120)
        logger.info(f"[{serial}] device visible par ADB")

        # Attente boot complet
        logger.info(f"[{serial}] wait-for-boot...")
        if not wait_for_boot(serial):
            logger.error(f"[{serial}] Boot timeout")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False
        logger.info(f"[{serial}] boot completed")

        logger.info(f"[{serial}] waiting for Android ready...")
        if not wait_for_android_ready(serial):
            logger.error(f"[{serial}] Android not ready")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False
        logger.info(f"[{serial}] Android ready")

        # Check for microG / GMS packages
        _GMS_PACKAGES = ["com.google.android.gms", "com.android.vending"]
        try:
            pm_out = subprocess.check_output(
                f"{ADB_BINARY} -s {serial} shell pm list packages",
                shell=True, text=True, timeout=10
            )
            installed = set(line.replace("package:", "").strip() for line in pm_out.splitlines())
            for pkg in _GMS_PACKAGES:
                if pkg in installed:
                    logger.info(f"[{serial}] GMS ✓ {pkg}")
                else:
                    logger.warning(f"[{serial}] GMS ✗ {pkg} NOT installed — run Scripts/setup_microg.py")
        except Exception as e:
            logger.warning(f"[{serial}] Unable to check GMS packages: {e}")

        # Wait for TCP port to open
        logger.info(f"[{serial}] waiting for TCP port {port_int}...")
        if not wait_for_tcp_port(port=port_int, timeout=60):
            logger.error(f"[{serial}] TCP port {port_int} unavailable")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False
        logger.info(f"[{serial}] port TCP {port_int} OK")

        time.sleep(5)

        # Root configuration
        logger.info(f"[{serial}] Configuring root environment...")
        if not ensure_root_environment(serial):
            logger.error(f"[{serial}] unable to get root")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        # Inject mitmproxy cert as system cert (bind mount, does not survive reboot)
        logger.info(f"[{serial}] Injecting mitmproxy cert as system cert...")

        r1 = subprocess.run(
            f"{ADB_BINARY} -s {serial} shell 'cp -r /system/etc/security/cacerts /data/local/tmp/cacerts 2>/dev/null || true'",
            shell=True, timeout=15, capture_output=True, text=True
        )
        logger.info(f"[{serial}] [cert] cp cacerts -> {r1.returncode} {r1.stderr.strip() or 'OK'}")

        # Push the cert directly from the host (source of truth = ~/.mitmproxy/mitmproxy-ca-cert.pem)
        mitm_cert_host = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
        r2 = subprocess.run(
            f"{ADB_BINARY} -s {serial} push {mitm_cert_host} /data/local/tmp/cacerts/c8750f0d.0",
            shell=True, timeout=10, capture_output=True, text=True
        )
        if r2.returncode == 0:
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell 'chmod 644 /data/local/tmp/cacerts/c8750f0d.0'",
                shell=True, timeout=5, capture_output=True, text=True
            )
        logger.info(f"[{serial}] [cert] push mitmproxy-ca-cert.pem -> {r2.returncode} {r2.stderr.strip() or 'OK'}")

        r3 = subprocess.run(
            f"{ADB_BINARY} -s {serial} shell 'su 0 mount --bind /data/local/tmp/cacerts /system/etc/security/cacerts'",
            shell=True, timeout=10, capture_output=True, text=True
        )
        logger.info(f"[{serial}] [cert] mount --bind -> {r3.returncode} {r3.stderr.strip() or 'OK'}")

        # Final verification
        r4 = subprocess.run(
            f"{ADB_BINARY} -s {serial} shell 'ls /system/etc/security/cacerts/ | grep c8750f0d'",
            shell=True, timeout=10, capture_output=True, text=True
        )
        if r4.returncode == 0 and "c8750f0d" in r4.stdout:
            logger.info(f"[{serial}] mitmproxy cert injected as system cert ✓")
        else:
            logger.warning(f"[{serial}] mitmproxy cert NOT found in system certs ✗ (stdout={r4.stdout.strip()})")

        # Clean up residual proxy settings
        logger.info(f"[{serial}] Cleaning up residual proxy settings...")
        for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell settings delete global {key}",
                shell=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        logger.info(f"[{serial}] Stabilizing ({STABILIZATION_DELAY}s)...")
        time.sleep(STABILIZATION_DELAY)

        logger.info(f"[{serial}] Checking popups ({POPUP_CHECK_DELAY}s)...")
        time.sleep(POPUP_CHECK_DELAY)

        with ADB_LOCK:
            adb_utils.dismiss_not_responding_popup(serial)
            logger.info(f"[{serial}] Removing third-party apps...")
            adb_utils.uninstall_all_third_party_packages(serial)

        if not is_device_online(serial):
            logger.error(f"[{serial}] offline after stabilization")
            EMULATOR_STATES[serial] = "OFFLINE"
            Database.update_emulator_status(serial, "OFFLINE")
            return False

        EMULATOR_STATES[serial] = "RUNNING"
        Database.update_emulator_status(serial, "RUNNING")
        logger.info(f"[{serial}] ready (ROOT)")
        return True

    finally:
        lock.release()


# ==========================================================
# ===================== WATCHDOG ===========================
# ==========================================================

def emulator_watchdog(avd_mapping, interval=15):
    """Monitors all ROOT emulators and restarts them if necessary."""
    while not STOP_EVENT.is_set():
        for serial in avd_mapping:
            state = EMULATOR_STATES.get(serial, "OFFLINE")
            if state == "STARTING":
                continue
            if not is_device_online(serial):
                if state == "RUNNING":
                    logger.warning(f"[{serial}] crashed (was RUNNING), restarting...")
                else:
                    logger.warning(f"[{serial}] offline, attempting restart")
                restart_emulator(serial, avd_mapping)
        time.sleep(interval)


# ==========================================================
# ===================== UTILITAIRES ========================
# ==========================================================

def create_health_check(serial):
    """
    Creates a health check function for an emulator.
    Raises an exception if the emulator is offline or a global shutdown is requested.
    """
    def health_check():
        if STOP_EVENT.is_set():
            raise InterruptedError(f"[{serial}] Global shutdown requested")
        if not is_device_online(serial):
            raise RuntimeError(f"[{serial}] Emulator offline (health check)")
        if EMULATOR_STATES.get(serial) != "RUNNING":
            raise RuntimeError(f"[{serial}] Emulator state: {EMULATOR_STATES.get(serial)}")
        return True
    return health_check


def _read_har_file(har_path: str, device_id: str):
    """
    Reads the HAR file from disk and returns its parsed content.
    Deletes the file after reading (success or error).

    Returns:
        dict if network entries were captured, None otherwise
    """
    if not os.path.exists(har_path) or os.path.getsize(har_path) == 0:
        logger.info(f"[{device_id}] No HAR file generated")
        return None

    try:
        with open(har_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            logger.info(f"[{device_id}] HAR file is empty")
            return None

        har_content = json.loads(content)
        entries_count = len(har_content.get("log", {}).get("entries", []))
        if entries_count > 0:
            logger.info(f"[{device_id}] HAR captured: {entries_count} network entry/entries")
            return har_content
        else:
            logger.info(f"[{device_id}] HAR empty (no network matches)")
            return None

    except json.JSONDecodeError as e:
        logger.warning(f"[{device_id}] Corrupted HAR file: {e}")
        return None
    except Exception as e:
        logger.warning(f"[{device_id}] Error reading HAR: {e}")
        return None
    finally:
        try:
            os.remove(har_path)
        except Exception:
            pass


def _reset_network(serial: str):
    """Resets network via airplane mode ON/OFF."""
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
        logger.info(f"[{serial}] Network reset OK")
    except subprocess.TimeoutExpired:
        logger.warning(f"[{serial}] Timeout on network reset")
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
                logger.error(f"[{serial}] Unable to disable airplane mode: {e}")


def _cleanup_after_analysis(serial: str, package_id: str, proxy_port: int):
    """Systematic post-analysis cleanup: proxy, network, uninstall."""
    cleanup_orphan_processes(proxy_port, serial)

    if not is_device_online(serial):
        logger.warning(f"[{serial}] Emulator offline, skipping cleanup")
        return

    # Remove proxy
    try:
        for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell settings delete global {key}",
                shell=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        logger.info(f"[{serial}] Proxy removed")
    except subprocess.TimeoutExpired:
        logger.warning(f"[{serial}] Timeout removing proxy")

    # Network reset
    _reset_network(serial)

    # Uninstall
    try:
        subprocess.run(
            f"{ADB_BINARY} -s {serial} uninstall {package_id}",
            shell=True, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        logger.info(f"[{serial}] Uninstalled {package_id} OK")
    except subprocess.TimeoutExpired:
        logger.warning(f"[{serial}] Timeout uninstalling {package_id}")

    # Close residual apps + check airplane mode
    adb_utils.close_all_apps(serial)
    logger.info(f"[{serial}] Residual apps closed")
    if adb_utils.disable_airplane_mode_if_on(serial):
        logger.warning(f"[{serial}] Airplane mode was active — disabled before next analysis")

    # Check host connectivity
    if not check_host_internet_connectivity():
        logger.warning(f"[{serial}] Host connectivity lost after cleanup!")
        restore_host_connectivity(serial, proxy_port)


# ==========================================================
# ===================== ROOT WORKER ========================
# ==========================================================

def root_worker(serial):
    """
    ROOT worker: fetches packages from packages_full_pipeline
    (frida_analyze IS NULL), installs from local folder, analyzes,
    stores the result and marks as complete.
    Stops when there are no more packages to process.
    """
    proxy_port = ROOT_CONFIG[serial]

    logger.info(f"[ROOT] Worker started for {serial}")

    # Wait for emulator to be ready
    while not is_device_online(serial) or EMULATOR_STATES.get(serial) != "RUNNING":
        logger.info(f"[{serial}] waiting for startup (state={EMULATOR_STATES.get(serial)})...")
        time.sleep(2)

    # Initial proxy cleanup
    logger.info(f"[{serial}] Initial proxy cleanup...")
    try:
        for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
            subprocess.run(
                f"{ADB_BINARY} -s {serial} shell settings delete global {key}",
                shell=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception as e:
        logger.warning(f"[{serial}] Error cleaning up initial proxy: {e}")

    while not STOP_EVENT.is_set():
        # --- Check emulator state ---
        if not is_device_online(serial) or EMULATOR_STATES.get(serial) != "RUNNING":
            logger.warning(f"[{serial}] Emulator unavailable, waiting...")
            time.sleep(5)
            continue

        # --- Claim next package ---
        package_id = Database.get_next_package_for_analysis()
        if package_id is None:
            logger.info(f"[{serial}] No more packages to analyze, waiting 10 min...")
            STOP_EVENT.wait(timeout=600)
            continue

        logger.info(f"[{serial}] Package claimed: {package_id}")
        analysis_completed = False

        # Rotate capture_all.har → capture_all_previous.har before new analysis
        all_har = os.path.join("temp", serial, "capture_all.har")
        all_har_prev = os.path.join("temp", serial, "capture_all_previous.har")
        if os.path.exists(all_har):
            try:
                shutil.move(all_har, all_har_prev)
                logger.info(f"[{serial}] capture_all.har saved → capture_all_previous.har")
            except Exception as e:
                logger.warning(f"[{serial}] Unable to save capture_all.har: {e}")

        # Delete residual HAR/jsonl files
        for fname in ("capture.har", "capture.jsonl"):
            fpath = os.path.join("temp", serial, fname)
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    logger.info(f"[{serial}] Residual file deleted: {fname}")
                except Exception as e:
                    logger.warning(f"[{serial}] Unable to delete {fname}: {e}")

        try:
            # --- Check state before processing ---
            if not is_device_online(serial) or EMULATOR_STATES.get(serial) != "RUNNING":
                logger.error(f"[{serial}] offline before processing {package_id}, requeueing")
                Database.reset_package_to_pending(package_id)
                time.sleep(5)
                continue

            # --- Check host connectivity ---
            if not check_host_internet_connectivity():
                logger.warning(f"[{serial}] Host connectivity lost before analysis!")
                if not restore_host_connectivity(serial, proxy_port):
                    logger.error(f"[{serial}] Host connectivity KO, requeueing {package_id}")
                    Database.reset_package_to_pending(package_id)
                    time.sleep(30)
                    continue

            # Nettoyage processus orphelins
            cleanup_orphan_processes(proxy_port, serial)

            # Pre-analysis proxy cleanup
            try:
                for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
                    subprocess.run(
                        f"{ADB_BINARY} -s {serial} shell settings delete global {key}",
                        shell=True, timeout=10,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
            except subprocess.TimeoutExpired:
                logger.warning(f"[{serial}] Timeout on pre-analysis proxy cleanup")

            # --- Install from local folder ---
            if not install_from_local(package_id, PACKAGES_BASE_PATH, serial):
                logger.error(f"[{serial}] Installation failed for {package_id}")
                Database.set_frida_error(package_id, "INSTALL_FAILED",
                                         explicit_result=_get_explicit_label("INSTALL_FAILED"))
                analysis_completed = True
                Database.increment_emulator_error(serial)
                continue

            # --- Frida + mitmproxy analysis ---
            health_check = create_health_check(serial)
            resp = analyze_app_with_timeout(
                package_id, serial, proxy_port,
                timeout=360, health_check=health_check
            )
            logger.info(f"[{serial}] Analysis complete: {package_id} → {resp}")

            # --- Read the HAR (left by analyze_app) ---
            har_path = os.path.join("temp", serial, "capture.har")
            har_data = _read_har_file(har_path, serial)

            # --- Record the result ---
            explicit = _get_explicit_label(resp or "")
            if resp and resp.startswith(("ERROR_", "TIMEOUT", "UNKOWN_")):
                Database.set_frida_error(package_id, resp, explicit_result=explicit)
            else:
                Database.complete_package_analysis(package_id, result=har_data, explicit_result=explicit)
            analysis_completed = True
            Database.increment_emulator_finished(serial)

        except PackageServiceDeadError as e:
            logger.error(f"[{serial}] {e} — marking OFFLINE for watchdog restart")
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
            logger.warning(f"[{serial}] Health check failed for {package_id}: {e}")
            if not analysis_completed:
                MAX_EMULATOR_CRASHES = 2
                count = _crash_counts.get(package_id, 0) + 1
                _crash_counts[package_id] = count
                if count >= MAX_EMULATOR_CRASHES:
                    logger.error(
                        f"[{serial}] {package_id} crashed the emulator {count}x "
                        f"→ marked EMULATOR_CRASH"
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
                        f"[{serial}] {package_id} → requeueing "
                        f"(emulator crash {count}/{MAX_EMULATOR_CRASHES})"
                    )
                    Database.reset_package_to_pending(package_id)
            time.sleep(2)

        except subprocess.CalledProcessError as e:
            msg = f"ADB_ERROR: {e.returncode}"
            logger.error(f"[{serial}] ADB error for {package_id}: {e}")
            Database.set_frida_error(package_id, msg,
                                     explicit_result=_get_explicit_label(msg))
            analysis_completed = True
            Database.increment_emulator_error(serial)

        except Exception as e:
            msg = f"UNEXPECTED: {type(e).__name__}: {str(e)[:200]}"
            logger.exception(f"[{serial}] Unexpected error for {package_id}")
            if not analysis_completed:
                Database.set_frida_error(package_id, msg,
                                         explicit_result=_get_explicit_label(msg))
                analysis_completed = True
            Database.increment_emulator_error(serial)

        finally:
            if package_id:
                Database.touch_frida_analyze_at(package_id)
                logger.info(f"[{serial}] Post-analysis cleanup: {package_id}")
                _cleanup_after_analysis(serial, package_id, proxy_port)

    logger.info(f"[{serial}] ROOT worker finished")


# ==========================================================
# ===================== MAIN ===============================
# ==========================================================

if __name__ == "__main__":
    multiprocessing.freeze_support()

    config.print_config()
    config.setup_logging()

    # Register cleanup handler only in the main process
    atexit.register(cleanup_on_exit)

    # --- Get ROOT emulators only ---
    ROOT_EMULATORS = [s for s, i in AVD_MAPPING.items() if i["type"] == "ROOT"]
    ALL_DEVICES = ROOT_EMULATORS

    # --- Dedicated log per emulator ---
    for serial in ALL_DEVICES:
        config.setup_emulator_logger(serial)

    if not ROOT_EMULATORS:
        logger.error("No ROOT emulator configured in AVD_MAPPING. Exiting.")
        sys.exit(1)

    logger.info(f"ROOT emulators configured: {ROOT_EMULATORS}")
    logger.info(f"Dossier APKs: {PACKAGES_BASE_PATH}")

    # --- Reset stuck packages (from previous crash) ---
    try:
        with Database.get_cursor() as cur:
            cur.execute(
                "UPDATE packages_full_pipeline SET frida_analyze = NULL WHERE frida_analyze = FALSE;"
            )
            count = cur.rowcount
        if count:
            logger.info(f"{count} stuck package(s) (frida_analyze=FALSE) requeued")
    except Exception as e:
        logger.warning(f"Unable to reset stuck packages: {e}")

    # --- Initialize states, locks and DB ---
    Database.reset_emulators()
    for serial in ALL_DEVICES:
        EMULATOR_STATES[serial] = "OFFLINE"
        EMULATOR_LOCKS[serial]  = threading.Lock()
        Database.add_emulator(serial, "Root", status="OFFLINE")

    # --- ROOT proxy ports ---
    for idx, serial in enumerate(ROOT_EMULATORS):
        ROOT_CONFIG[serial] = config.ROOT_PORT_START + idx

    # --- Temporary directories ---
    for serial in ALL_DEVICES:
        shutil.rmtree(os.path.join("temp", serial), ignore_errors=True)
        os.makedirs(os.path.join("temp", serial), exist_ok=True)

    # --- Live view ---
    live_view = LiveViewServer(devices=ALL_DEVICES)
    live_view.start()

    # --- Start ADB daemon ---
    logger.info("Checking ADB daemon...")
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
        logger.info("ADB daemon ready")
    except Exception as e:
        logger.warning(f"ADB daemon error: {e} — attempting to continue anyway...")

    # --- Start emulators in parallel ---
    logger.info("Starting ROOT emulators in parallel...")

    def start_emulator(serial):
        success = restart_emulator(serial, AVD_MAPPING)
        if not success:
            logger.error(f"Unable to start {serial}")
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

    logger.info("Initial startup complete")

    # --- Watchdog (after initial startup) ---
    watchdog_thread = threading.Thread(
        target=emulator_watchdog,
        args=(AVD_MAPPING,),
        daemon=True
    )
    watchdog_thread.start()
    logger.info("Emulator watchdog active")

    # --- Launch ROOT workers ---
    for serial in ROOT_EMULATORS:
        while EMULATOR_STATES.get(serial) != "RUNNING":
            time.sleep(1)
        t = threading.Thread(target=root_worker, args=(serial,), name=f"ROOT-{serial}")
        t.start()
        WORKER_THREADS.append(t)
        logger.info(f"[ROOT] Worker started for {serial}")

    logger.info("PIPELINE ACTIF — En attente de la fin des analyses...")

    # --- Main loop: wait for all workers to finish ---
    try:
        for t in WORKER_THREADS:
            while t.is_alive():
                t.join(timeout=1)
                # Allows Ctrl+C detection even during join
    except KeyboardInterrupt:
        logger.info("Shutdown requested (Ctrl+C)...")
        STOP_EVENT.set()

        logger.info("Waiting for workers to finish...")
        for t in WORKER_THREADS:
            t.join(timeout=30)
            if t.is_alive():
                logger.warning(f"Thread {t.name} not responding, abandoning")

    logger.info("Shutting down emulators...")
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

    logger.info("Done — no more packages to analyze.")
