import asyncio
import hashlib
import logging
import subprocess
import sys
import time
import multiprocessing
import os
import json
import socket
from enum import Enum
from mitmproxy.tools.dump import DumpMaster
from mitmproxy.options import Options
import re
import shutil
import threading
import adb_utils
import utils_openai
import Database
from config import DEBUG_PROXY, DEBUG_PROXY_FILE

# Absolute path to the frida binary in the current venv
# Works in both interactive sessions and with nohup
FRIDA_BINARY = os.path.join(os.path.dirname(sys.executable), "frida")

logger = logging.getLogger(__name__)


def wait_for_tcp_port(host="127.0.0.1", port=8080, timeout=30):
    """Waits for the TCP port to be open (proxy ready)."""
    start = time.time()
    while time.time() - start < timeout:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect((host, port))
            s.close()
            return True
        except (socket.error, socket.timeout):
            time.sleep(0.5)
        finally:
            s.close()
    return False

# --- CONFIGURATION ---
BASE_PATH = os.path.abspath(".")

# --- EXCEPTIONS ---
class FridaCrashError(Exception):
    """Raised when Frida detects a process crash."""
    def __init__(self, error_type, message):
        self.error_type = error_type  # Ex: "TRACE_BPT_TRAP", "BAD_ACCESS"
        self.message = message
        super().__init__(f"FRIDA_ERROR_{error_type}: {message}")

class ChromeForegroundError(Exception):
    """Raised when Chrome is detected in the foreground."""
    pass

class AppQuitError(Exception):
    """Raised when the analyzed app is no longer in the foreground."""
    pass


def check_foreground(device_id: str, package_id: str):
    """
    Checks that the analyzed app is in the foreground.
    Raises ChromeForegroundError if Chrome is detected.
    Raises AppQuitError if another (non-system) app is in the foreground.
    Does nothing if the foreground cannot be determined.
    """
    fg = adb_utils.get_foreground_package(device_id)
    if not fg:
        return
    if "chrome" in fg.lower():
        raise ChromeForegroundError(f"Chrome detected in foreground on {device_id}")
    if package_id and fg != package_id:
        raise AppQuitError(f"[{device_id}] App {package_id} quit — foreground: {fg}")


class AnalysisResult(str, Enum):
    END_EMAIL_UNIQUE_NO_SUBMIT  = "END_EMAIL_UNIQUE_NO_SUBMIT_POSITIVE"
    END_EMAIL_UNIQUE_SUBMIT     = "END_EMAIL_UNIQUE_SUBMIT_POSITIVE"
    END_EMAIL_MDP_OK            = "END_EMAIL_MDP_OK_POSITIVE"
    END_EMAIL_OK                = "END_EMAIL_OK_POSITIVE"
    END_NO_INFO_REGISTER        = "END_NO_INFO_REGISTER"
    NO_LOGIN                    = "NO_LOGIN"
    NO_REGISTER                 = "NO_REGISTER"
    FAILED_GO_TO_LOGIN          = "FAILED_GO_TO_LOGIN"
    UNKNOWN_ENDING              = "UNKOWN_ENDING"
    ERROR_CHROME                = "ERROR_CHROME"
    APP_QUIT                    = "APP_QUIT"
    ERROR_EMAIL_CHECK_VALUE     = "ERROR_EMAIL_CHECK_VALUE"
    ERROR_EMAIL_REGISTER_PAGE   = "ERROR_EMAIL_REGISTER_PAGE"
    ERROR_EMAIL_REGISTER_VALUE  = "ERROR_EMAIL_REGISTER_CHECK_VALUE"
    ERROR_REGISTER_NO_EMAIL     = "ERROR_REGISTER_NO_EMAIL"
    ERROR_REGISTER_PAGE         = "ERROR_REGISTER_PAGE"
    ERROR_REGISTER_EMAIL_VALUE  = "ERROR_REGISTER_EMAIL_PAGE_VALUE"
    PLAY_STORE_REQUIRED         = "PLAY_STORE_REQUIRED"


def check_chrome_foreground(device_id: str):
    """Checks if Chrome is in the foreground. Raises ChromeForegroundError if so."""
    if adb_utils.is_chrome_foreground(device_id):
        raise ChromeForegroundError(f"Chrome detected in foreground on {device_id}")


# Patterns to detect Frida errors
FRIDA_CRASH_PATTERNS = {
    "TRACE_BPT_TRAP": r"Process crashed: Trace/BPT trap",
    "BAD_ACCESS": r"Process crashed: Bad access",
    "SEGFAULT": r"Process crashed: Segmentation fault",
    "SIGABRT": r"Process crashed: Aborted",
    "SIGKILL": r"Process crashed: Killed",
    "PROCESS_TERMINATED": r"Process terminated",
    "FRIDA_SERVER_NOT_RUNNING": r"Failed to spawn|Unable to connect to remote frida-server",
    # New patterns for Java/Native errors
    "UNSATISFIED_LINK": r"Process crashed:.*UnsatisfiedLinkError",
    "FATAL_EXCEPTION": r"FATAL EXCEPTION:",
    # "FRIDA_SESSION_ENDED" removed: "Thank you for using Frida!" is a normal exit message, not a crash
    "FRIDA_PYTHON_ERROR": r"Fatal Python error:(?!.*_enter_buffered_busy.*interpreter shutdown)",
    "DLOPEN_FAILED": r"dlopen failed:",
    "STARTUP_ERROR": r"Error logged during startup",
    "APP_CRASH": r"Process crashed:",
}


class FridaMonitor:
    """Monitors Frida output to detect crashes."""

    def __init__(self, process, device_id):
        self.process = process
        self.device_id = device_id
        self.crash_detected = threading.Event()
        self.crash_error = None
        self._stop_event = threading.Event()
        self._terminating = False  # True when Frida is being intentionally stopped
        self._monitor_thread = None
        self._stderr_thread = None
        self._stdout_thread = None

    def set_terminating(self):
        """Signals that Frida is being voluntarily stopped — disables crash detection."""
        self._terminating = True

    def start(self):
        """Starts background monitoring with separate threads for stdout/stderr."""
        if self.process.stderr:
            self._stderr_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stderr, "stderr"),
                daemon=True
            )
            self._stderr_thread.start()

        if self.process.stdout:
            self._stdout_thread = threading.Thread(
                target=self._read_stream,
                args=(self.process.stdout, "stdout"),
                daemon=True
            )
            self._stdout_thread.start()

    def stop(self):
        """Stops monitoring."""
        self._stop_event.set()
        # Close streams to unblock readline() calls
        try:
            if self.process.stdout:
                self.process.stdout.close()
        except:
            pass
        try:
            if self.process.stderr:
                self.process.stderr.close()
        except:
            pass

    def check_crash(self):
        """Checks if a crash has been detected. Raises FridaCrashError if so."""
        if self.crash_detected.is_set() and self.crash_error:
            raise self.crash_error

    TLS_NOISE = (
        "Unexpected TLS failure",
        "CertificateException",
        "CertPathValidatorException",
        "TrustManagerImpl",
        "Unrecognized TLS error",
        "must be patched manually",
    )

    def _read_stream(self, stream, stream_name):
        """Reads a stream (stdout or stderr) line by line."""
        try:
            while not self._stop_event.is_set():
                try:
                    line = stream.readline()
                    if not line:
                        # Stream closed or EOF
                        break
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if line_str:
                        if any(noise in line_str for noise in self.TLS_NOISE):
                            continue
                        if not self._terminating:
                            logger.info(f"[Frida {self.device_id}] {line_str}")
                        self._check_for_crash(line_str)
                except (ValueError, OSError):
                    # Stream closed during read
                    break
        except Exception as e:
            if not self._stop_event.is_set():
                logger.warning(f"[FridaMonitor {self.device_id}] Error {stream_name}: {e}")

    def _check_for_crash(self, line):
        """Checks if the line contains a crash error."""
        if self._terminating or self.crash_detected.is_set():
            return
        for error_type, pattern in FRIDA_CRASH_PATTERNS.items():
            if re.search(pattern, line, re.IGNORECASE):
                if error_type == "PROCESS_TERMINATED":
                    logger.info(f"[Frida {self.device_id}] App closed (PROCESS_TERMINATED)")
                else:
                    logger.error(f"💥 [Frida {self.device_id}] CRASH DETECTED: {error_type}")
                self.crash_error = FridaCrashError(error_type, line)
                self.crash_detected.set()
                return

class SimpleCaptureAddon:
    # Terms to search in requests (email encoded or not)
    SEARCH_TERMS = [
        "test@gmail.com",
        "test%40gmail.com",
        "dGVzdEBnbWFpbC5jb20=",            # base64
        "1aedb8d9dc4751e229a335e371db8058",  # MD5
        "87924606b4131a8aceeeae8868531fbb9712aaa07a5d3a756b26ce0f5d6ca674",  # SHA256
    ]

    def __init__(self, output_file):
        # Output file will be .har instead of .jsonl
        self.output_file = output_file.replace(".jsonl", ".har")
        self.entries = []
        self.start_time = time.time()
        self.debug_entries = []
        self.debug_file = DEBUG_PROXY_FILE if DEBUG_PROXY else None
        # Unconditional archive of all requests (debug)
        self.archive_file = self.output_file.replace(".har", "_all.har")
        self.archive_entries = []

    def _create_har_entry(self, flow):
        """Converts a mitmproxy flow to a standard HAR entry."""
        import datetime

        # Headers in HAR format
        req_headers = [{"name": k, "value": v} for k, v in flow.request.headers.items()]
        res_headers = [{"name": k, "value": v} for k, v in flow.response.headers.items()]

        # Request body
        req_body_text = ""
        try:
            req_body_text = flow.request.text if flow.request.text else ""
        except:
            req_body_text = ""

        # Response body
        res_body_text = ""
        try:
            res_body_text = flow.response.text if flow.response.text else ""
        except:
            res_body_text = ""

        # Calcul des timings
        started = datetime.datetime.fromtimestamp(flow.request.timestamp_start).isoformat() + "Z"
        total_time = (flow.response.timestamp_end - flow.request.timestamp_start) * 1000  # in ms

        entry = {
            "startedDateTime": started,
            "time": total_time,
            "request": {
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "httpVersion": flow.request.http_version,
                "headers": req_headers,
                "queryString": [{"name": k, "value": v} for k, v in flow.request.query.items()],
                "postData": {
                    "mimeType": flow.request.headers.get("content-type", ""),
                    "text": req_body_text
                },
                "headersSize": -1,
                "bodySize": len(flow.request.content) if flow.request.content else 0
            },
            "response": {
                "status": flow.response.status_code,
                "statusText": flow.response.reason,
                "httpVersion": flow.response.http_version,
                "headers": res_headers,
                "content": {
                    "size": len(flow.response.raw_content) if flow.response.raw_content else 0,
                    "mimeType": flow.response.headers.get("content-type", ""),
                    "text": res_body_text
                },
                "redirectURL": flow.response.headers.get("location", ""),
                "headersSize": -1,
                "bodySize": len(flow.response.raw_content) if flow.response.raw_content else 0
            },
            "cache": {},
            "timings": {
                "send": 0,
                "wait": total_time,
                "receive": 0
            }
        }
        return entry

    def _matches_search_terms(self, flow):
        """Checks if the flow contains any of the search terms."""
        # Check in URL
        url = flow.request.pretty_url
        if any(term in url for term in self.SEARCH_TERMS):
            return True

        # Check in request body
        try:
            req_body = flow.request.text or ""
            if any(term in req_body for term in self.SEARCH_TERMS):
                return True
        except:
            pass

        # Check in response body
        try:
            res_body = flow.response.text or ""
            if any(term in res_body for term in self.SEARCH_TERMS):
                return True
        except:
            pass

        return False

    def http_connect(self, flow):
        # Debug: log all CONNECT attempts (before TLS, before request)
        if self.debug_file:
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "host": flow.request.pretty_host,
                "port": flow.request.port,
                "stage": "connect",
                "tls_status": "pending",
            }
            self.debug_entries.append(entry)
            with open(self.debug_file, "w", encoding="utf-8") as f:
                json.dump(self.debug_entries, f, indent=2, ensure_ascii=False)

    def tls_failed_client(self, tls_start):
        # Called when TLS handshake fails on client side (Android app → mitmproxy)
        if not self.debug_file:
            return
        try:
            addr = tls_start.context.server.address
            host = addr[0] if addr else "unknown"
            port = addr[1] if addr else 0
            error = str(tls_start.error) if getattr(tls_start, "error", None) else "TLS handshake failed"
            # Update the matching connect entry (most recent for this host:port)
            for entry in reversed(self.debug_entries):
                if entry.get("stage") == "connect" and entry.get("host") == host and entry.get("port") == port and entry.get("tls_status") == "pending":
                    entry["tls_status"] = "failed"
                    entry["tls_error"] = error
                    break
            with open(self.debug_file, "w", encoding="utf-8") as f:
                json.dump(self.debug_entries, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def tls_established_client(self, tls_start):
        # Called when TLS handshake succeeds on client side
        if not self.debug_file:
            return
        try:
            addr = tls_start.context.server.address
            host = addr[0] if addr else "unknown"
            port = addr[1] if addr else 0
            for entry in reversed(self.debug_entries):
                if entry.get("stage") == "connect" and entry.get("host") == host and entry.get("port") == port and entry.get("tls_status") == "pending":
                    entry["tls_status"] = "ok"
                    break
            with open(self.debug_file, "w", encoding="utf-8") as f:
                json.dump(self.debug_entries, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def request(self, flow):
        # Debug: log all incoming requests (TLS succeeded)
        if self.debug_file:
            body_preview = ""
            try:
                raw = flow.request.text or ""
                body_preview = raw[:500] + ("…" if len(raw) > 500 else "")
            except Exception:
                pass
            has_email = any(t in (flow.request.pretty_url + body_preview) for t in self.SEARCH_TERMS)
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "stage": "request",
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "content_type": flow.request.headers.get("content-type", ""),
                "body_preview": body_preview,
                "contains_email": has_email,
            }
            self.debug_entries.append(entry)
            with open(self.debug_file, "w", encoding="utf-8") as f:
                json.dump(self.debug_entries, f, indent=2, ensure_ascii=False)

    def response(self, flow):
        url = flow.request.pretty_url

        # Debug: enrich the existing request() entry with the response
        if self.debug_file:
            res_body_preview = ""
            try:
                raw = flow.response.text or ""
                res_body_preview = raw[:500] + ("…" if len(raw) > 500 else "")
            except Exception:
                pass
            has_email = self._matches_search_terms(flow)
            # Find and update the matching request entry
            updated = False
            for entry in reversed(self.debug_entries):
                if entry.get("stage") == "request" and entry.get("url") == url:
                    entry["stage"] = "response"
                    entry["status"] = flow.response.status_code
                    entry["status_text"] = flow.response.reason
                    entry["response_content_type"] = flow.response.headers.get("content-type", "")
                    entry["response_body_preview"] = res_body_preview
                    entry["contains_email"] = has_email
                    updated = True
                    break
            if not updated:
                # No prior request entry (e.g. response without a logged request)
                self.debug_entries.append({
                    "ts": time.strftime("%H:%M:%S"),
                    "stage": "response",
                    "method": flow.request.method,
                    "url": url,
                    "status": flow.response.status_code,
                    "status_text": flow.response.reason,
                    "response_content_type": flow.response.headers.get("content-type", ""),
                    "response_body_preview": res_body_preview,
                    "contains_email": has_email,
                })
            with open(self.debug_file, "w", encoding="utf-8") as f:
                json.dump(self.debug_entries, f, indent=2, ensure_ascii=False)

        # Unconditional archive of all requests (for debug)
        archive_entry = self._create_har_entry(flow)
        self.archive_entries.append(archive_entry)
        self._save_archive_file()

        # Filtrage du bruit Google
        if any(x in url for x in ["google.com", "gstatic.com", "googleapis.com"]):
            return

        # Only store if it matches the search terms
        if not self._matches_search_terms(flow):
            return

        logger.info(f"[HAR CAPTURED - MATCH] {url}")

        # Create the HAR entry and add it to the list
        har_entry = self._create_har_entry(flow)
        self.entries.append(har_entry)

        # Save the full HAR file on each new entry
        self._save_har_file()

    def _save_har_file(self):
        """Saves the full HAR file."""
        har_document = {
            "log": {
                "version": "1.2",
                "creator": {
                    "name": "Email_Android_Module",
                    "version": "1.0"
                },
                "entries": self.entries
            }
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(har_document, f, indent=2, ensure_ascii=False)

    def _save_archive_file(self):
        """Saves the full archive of all requests (unfiltered)."""
        har_document = {
            "log": {
                "version": "1.2",
                "creator": {
                    "name": "Email_Android_Module_Archive",
                    "version": "1.0"
                },
                "entries": self.archive_entries
            }
        }

        with open(self.archive_file, "w", encoding="utf-8") as f:
            json.dump(har_document, f, indent=2, ensure_ascii=False)


def start_proxy_process(path, device_id):
    asyncio.run(run_proxy_logic(path, device_id))

async def run_proxy_logic(output_path, port):
    try:
        opts = Options(listen_host='0.0.0.0', listen_port=port)
        master = DumpMaster(opts, with_dumper=False, with_termlog=False)
        # Niveau WARNING pour voir les erreurs TLS mitmproxy (DEBUG temporaire)
        logging.getLogger("mitmproxy").setLevel(logging.WARNING)
        # Pass the path specific to this emulator/package
        master.addons.add(SimpleCaptureAddon(output_path))
        logger.info(f"mitmproxy started on port {port}")
    except Exception as e:
        logger.error(f"Error starting proxy on port {port}: {e}")
        return

    try:
        await master.run()
    except Exception as e:
        logger.error(f"Erreur Proxy sur port {port}: {e}")
    finally:
        master.shutdown()
# --- MAIN ---
# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _snapshot(device_id: str, package_id: str = ""):
    """check_foreground (chrome + app quit) + take_android_screenshot."""
    check_foreground(device_id, package_id)
    adb_utils.take_android_screenshot(device_id)


def _focus_field(device_id: str, x: float, y: float):
    """Taps a text field only if the keyboard is not already visible."""
    if not adb_utils.is_keyboard_active(device_id):
        adb_utils.tap(device_id, x=x, y=y)
        time.sleep(5)


def _tap_submit(device_id: str, resp: dict, screen_w: int, screen_h: int) -> bool:
    """Taps the submit button if it exists. Returns True if clicked."""
    if resp["submit_button"] == "NO_SUBMIT_BUTTON":
        return False
    adb_utils.tap(
        device_id,
        x=screen_w * resp["submit_button"]["x"],
        y=screen_h * resp["submit_button"]["y"],
    )
    time.sleep(10)
    return True


# ---------------------------------------------------------------------------
# Navigation steps
# ---------------------------------------------------------------------------

def _check_play_store_popup(device_id: str) -> bool:
    """
    Checks if a "Play Store required" popup is displayed (CLOSE button detected).
    Takes a snapshot and reads the cleaned ui.json.
    Returns True if the popup is detected.
    """
    adb_utils.take_snapshot(device_id, screenshot=False, text_only=False)
    ui_path = os.path.join("temp", device_id, "ui.json")
    try:
        with open(ui_path, "r", encoding="utf-8") as f:
            ui_data = json.load(f)
    except Exception as e:
        logger.warning(f"[{device_id}] Cannot read ui.json for Play Store check: {e}")
        return False
    for el in ui_data.get("elements", []):
        if (
            el.get("type") == "android.widget.Button"
            and el.get("text") == "CLOSE"
            and el.get("clickable") is True
        ):
            logger.info(f"[{device_id}] Play Store required popup detected (CLOSE button found).")
            return True
    return False


def _ui_hash(device_id: str) -> str:
    try:
        with open(f"temp/{device_id}/ui.json", "r", encoding="utf-8") as f:
            return hashlib.md5(f.read().encode()).hexdigest()[:8]
    except Exception:
        return ""


def _navigate_to_login(
    device_id: str, screen_w: int, screen_h: int, package_id: str,
    max_attempts: int, health_check_callback, frida_monitor,
) -> "AnalysisResult | None":
    """
    Navigates to the login page.
    Returns None if found, otherwise an error AnalysisResult.
    """
    logger.info(f"[{device_id}] Starting login navigation (max {max_attempts} attempts)")
    already_tapped: list = []
    previous_hash = ""
    same_screen_count = 0
    for it in range(1, max_attempts + 1):
        if health_check_callback:
            health_check_callback()
        if frida_monitor:
            frida_monitor.check_crash()
        logger.info(f"[{device_id}] Navigation iteration {it}/{max_attempts}")
        check_foreground(device_id, package_id)
        resp = utils_openai.analyze_login_entry(
            device_id,
            add_screenshot=False,
            package_name=package_id,
            iteration=it,
            max_iterations=max_attempts,
            already_tapped=already_tapped,
            same_screen_count=same_screen_count,
        )
        logger.info(f"[{device_id}] AI response: etat={resp['etat']}")
        current_hash = _ui_hash(device_id)
        if current_hash and current_hash == previous_hash:
            same_screen_count += 1
        else:
            same_screen_count = 0
        previous_hash = current_hash

        if same_screen_count >= 3:
            logger.warning(f"[{device_id}] UI unchanged {same_screen_count} times in a row → FAILED_GO_TO_LOGIN")
            return AnalysisResult.FAILED_GO_TO_LOGIN

        if resp["etat"] == "NEED_SCREENSHOT":
            logger.info(f"[{device_id}] AI requests a screenshot, retrying with image...")
            check_foreground(device_id, package_id)
            resp = utils_openai.analyze_login_entry(
                device_id,
                add_screenshot=True,
                package_name=package_id,
                iteration=it,
                max_iterations=max_attempts,
                already_tapped=already_tapped,
                same_screen_count=same_screen_count,
            )
            logger.info(f"[{device_id}] AI response (with screenshot): etat={resp['etat']}")

        if resp["etat"] in ("MODALS", "OTHER"):
            where_tap = resp.get("where_tap")
            if not where_tap:
                logger.warning(f"[{device_id}] {resp['etat']} without where_tap, iteration skipped")
                time.sleep(2)
                continue
            if where_tap.get("action") == "BACK":
                logger.info(f"[{device_id}] Action BACK pour {resp['etat']}")
                adb_utils.adb_back(device_id)
            else:
                tap_x = screen_w * where_tap["x"]
                tap_y = screen_h * where_tap["y"]
                logger.info(f"[{device_id}] Tap sur ({tap_x:.0f}, {tap_y:.0f}) pour {resp['etat']}")
                adb_utils.tap(device_id, x=tap_x, y=tap_y)
                already_tapped.append({
                    "name": where_tap.get("name", ""),
                    "x": where_tap["x"],
                    "y": where_tap["y"],
                })
            time.sleep(5)
        elif resp["etat"] in ("NO_LOGIN", "NO_EMAIL_LOGIN"):
            logger.info(f"[{device_id}] {resp['etat']} — pas de login email, fin analyse")
            return AnalysisResult.NO_LOGIN
        elif resp["etat"] == "LOGIN_EMAIL":
            logger.info(f"[{device_id}] Login page found in {it} step(s).")
            return None
        else:
            logger.warning(f"[{device_id}] Unhandled state: {resp['etat']}")
    logger.warning(f"[{device_id}] Max attempts reached without finding login")
    return AnalysisResult.FAILED_GO_TO_LOGIN


def _navigate_to_register(
    device_id: str, screen_w: int, screen_h: int, package_id: str, max_attempts: int,
) -> bool:
    """Navigates to the register page. Returns True if found."""
    for _ in range(max_attempts):
        _snapshot(device_id, package_id)
        resp = utils_openai.analyze_go_to_register_page(device_id, package_name=package_id)
        if resp["etat"] == "TAP":
            adb_utils.tap(device_id, x=screen_w * resp["where_tap"]["x"], y=screen_h * resp["where_tap"]["y"])
            time.sleep(5)
        elif resp["etat"] == "PAGE_REGISTER":
            logger.info(f"[{device_id}] Found register page.")
            return True
        elif resp["etat"] == "NO_INFO":
            logger.info(f"[{device_id}] Register page not found. End.")
            return False
    return False


# ---------------------------------------------------------------------------
# Login / register handlers
# ---------------------------------------------------------------------------

def _handle_email_unique(
    device_id: str, resp: dict, screen_w: int, screen_h: int, package_id: str = "",
) -> AnalysisResult:
    _focus_field(device_id, screen_w * resp["email_field"]["x"], screen_h * resp["email_field"]["y"])
    adb_utils.type_text(device_id, text="test@gmail.com")
    time.sleep(2)
    adb_utils.press_enter(device_id)
    time.sleep(2)
    adb_utils.hide_keyboard(device_id)
    time.sleep(3)
    check_foreground(device_id, package_id)
    if _tap_submit(device_id, resp, screen_w, screen_h):
        logger.info(f"[{device_id}] Submitted fake email on login page (Email only).")
        return AnalysisResult.END_EMAIL_UNIQUE_SUBMIT
    logger.info(f"[{device_id}] No submit button on login page (Email only).")
    return AnalysisResult.END_EMAIL_UNIQUE_NO_SUBMIT


def _handle_register_page(
    device_id: str, screen_w: int, screen_h: int, package_id: str,
) -> AnalysisResult:
    _snapshot(device_id, package_id)
    resp = utils_openai.analyze_register_page(device_id, package_name=package_id)
    if resp["etat"] == "NO_EMAIL":
        logger.info(f"[{device_id}] No email field found on register page. End.")
        return AnalysisResult.ERROR_REGISTER_NO_EMAIL
    if resp["etat"] == "ERROR":
        logger.warning(f"[{device_id}] Error analyzing register page. End.")
        return AnalysisResult.ERROR_REGISTER_PAGE
    if resp["etat"] != "EMAIL":
        return AnalysisResult.ERROR_REGISTER_EMAIL_VALUE

    adb_utils.tap(device_id, x=screen_w * resp["email_field"]["x"], y=screen_h * resp["email_field"]["y"])
    time.sleep(5)
    adb_utils.type_text(device_id, text="test@gmail.com")
    time.sleep(5)
    adb_utils.press_enter(device_id)
    time.sleep(5)
    adb_utils.hide_keyboard(device_id)
    _snapshot(device_id, package_id)
    resp = utils_openai.analyze_email_exists(device_id, package_name=package_id)
    if resp["etat"] == "INFO_EMAIL":
        logger.info(f"[{device_id}] Email info found!")
        return AnalysisResult.END_EMAIL_OK
    if resp["etat"] == "NO_INFO_EMAIL":
        logger.info(f"[{device_id}] No email info found. End.")
        return AnalysisResult.END_NO_INFO_REGISTER
    if resp["etat"] == "ERROR":
        logger.warning(f"[{device_id}] Error analyzing register page.")
        return AnalysisResult.ERROR_EMAIL_REGISTER_PAGE
    return AnalysisResult.ERROR_EMAIL_REGISTER_VALUE


def _handle_email_mdp(
    device_id: str, resp: dict, screen_w: int, screen_h: int,
    package_id: str, max_register_attempts: int,
) -> AnalysisResult:
    _focus_field(device_id, screen_w * resp["email_field"]["x"], screen_h * resp["email_field"]["y"])
    adb_utils.type_text(device_id, text="test@gmail.com")
    time.sleep(2)
    adb_utils.press_enter(device_id)
    time.sleep(2)
    adb_utils.hide_keyboard(device_id)
    time.sleep(2)
    adb_utils.tap(device_id, x=screen_w * resp["password_field"]["x"], y=screen_h * resp["password_field"]["y"])
    time.sleep(5)
    adb_utils.type_text(device_id, text="password#1A")
    time.sleep(2)
    adb_utils.press_enter(device_id)
    time.sleep(2)
    adb_utils.hide_keyboard(device_id)
    time.sleep(3)
    submitted = _tap_submit(device_id, resp, screen_w, screen_h)
    logger.info(
        f"[{device_id}] {'Submitted fake email+password' if submitted else 'No submit button (Email + Password)'}. Analyzing page..."
    )
    _snapshot(device_id, package_id)
    resp = utils_openai.analyze_email_exists(device_id, package_name=package_id)
    if resp["etat"] == "INFO_EMAIL":
        logger.info(f"[{device_id}] Email info found!")
        return AnalysisResult.END_EMAIL_MDP_OK
    if resp["etat"] not in ("NO_INFO_EMAIL", "ERROR"):
        return AnalysisResult.ERROR_EMAIL_CHECK_VALUE
    logger.info(
        f"[{device_id}] {'Error on analyzing page' if resp['etat'] == 'ERROR' else 'No email info found'}. Try register page..."
    )
    check_foreground(device_id, package_id)
    if not _navigate_to_register(device_id, screen_w, screen_h, package_id, max_register_attempts):
        return AnalysisResult.NO_REGISTER
    return _handle_register_page(device_id, screen_w, screen_h, package_id)


# ---------------------------------------------------------------------------
# Analysis orchestrator
# ---------------------------------------------------------------------------

def _run_analysis(
    device_id: str, package_id: str, screen_w: int, screen_h: int,
    health_check_callback, frida_monitor,
) -> AnalysisResult:
    """Pure UI analysis logic — navigation, login, register."""
    MAX_LOGIN_ATTEMPTS    = 7
    MAX_REGISTER_ATTEMPTS = 3

    if _check_play_store_popup(device_id):
        return AnalysisResult.PLAY_STORE_REQUIRED

    nav_result = _navigate_to_login(
        device_id, screen_w, screen_h, package_id,
        MAX_LOGIN_ATTEMPTS, health_check_callback, frida_monitor,
    )
    if nav_result == AnalysisResult.FAILED_GO_TO_LOGIN:
        logger.info(f"[{device_id}] Login not found after {MAX_LOGIN_ATTEMPTS} attempts, trying register page...")
        check_foreground(device_id, package_id)
        if not _navigate_to_register(device_id, screen_w, screen_h, package_id, MAX_REGISTER_ATTEMPTS):
            return AnalysisResult.NO_REGISTER
        return _handle_register_page(device_id, screen_w, screen_h, package_id)
    if nav_result is not None:
        return nav_result

    if health_check_callback:
        health_check_callback()

    _snapshot(device_id, package_id)
    resp = utils_openai.analyze_login_page(device_id, package_name=package_id)
    logger.info(f"[{device_id}] Analyzing login page : {resp['etat']}")
    if resp["etat"] == "EMAIL_UNIQUE":
        return _handle_email_unique(device_id, resp, screen_w, screen_h, package_id)
    if resp["etat"] == "EMAIL_MDP":
        return _handle_email_mdp(device_id, resp, screen_w, screen_h, package_id, MAX_REGISTER_ATTEMPTS)
    if resp["etat"] == "NO_LOGIN":
        logger.info(f"[{device_id}] No login page found.")
        return AnalysisResult.NO_LOGIN
    return AnalysisResult.UNKNOWN_ENDING


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_app(package_id, device_id, port, health_check_callback=None):
    """
    Starts the proxy on a dedicated port, performs ADB analysis, and shuts everything down.

    Args:
        package_id: Package of the app to analyze
        device_id: Emulator serial
        port: Proxy port
        health_check_callback: Optional function that checks emulator health.
                              Must raise an exception if the emulator is offline.

    Returns:
        AnalysisResult (str enum)

    Raises:
        RuntimeError: If the emulator goes offline during analysis
        InterruptedError: If a global shutdown is requested
    """
    utils_openai.reset_token_counter()

    if health_check_callback:
        health_check_callback()

    log_file = os.path.abspath(os.path.join("temp", device_id, "capture.jsonl"))
    proxy_proc = multiprocessing.Process(target=start_proxy_process, args=(log_file, port))
    proxy_proc.daemon = True
    proxy_proc.start()

    logger.info(f"[{device_id}] Waiting for proxy to be ready on port {port}...")
    if wait_for_tcp_port(port=port, timeout=30):
        logger.info(f"[{device_id}] Proxy ready on port {port}")
    else:
        logger.warning(f"[{device_id}] Timeout waiting for proxy on port {port}, continuing anyway...")

    ADB_BINARY = "adb"
    logger.info(f"[{device_id}] Configuring Android proxy...")
    subprocess.run(
        f"{ADB_BINARY} -s {device_id} shell settings put global http_proxy :0",
        shell=True, check=True, timeout=10,
    )
    subprocess.run(
        f"{ADB_BINARY} -s {device_id} shell settings put global http_proxy 10.0.2.2:{port}",
        shell=True, check=True, timeout=10,
    )
    logger.info(f"[{device_id}] Android proxy configured on 10.0.2.2:{port}")

    logger.info(f"[{device_id}] Reset Frida server...")
    adb_utils.reset_Frida_server(device_id)
    logger.info(f"[{device_id}] Frida server ready")

    # Checkpoint 1
    if health_check_callback:
        health_check_callback()

    frida_proc = None
    frida_monitor = None
    try:
        screen_w, screen_h = adb_utils.get_emulator_size(device_id)
        adb_utils.disable_android_animations(device_id)

        # Checkpoint 2
        if health_check_callback:
            health_check_callback()

        # Generate the Frida config specific to this worker's port
        dynamic_config_path = os.path.abspath(os.path.join("temp", device_id, "config_root.js"))
        template_path = os.path.join(BASE_PATH, "Frida_hook/config.js")
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("const PROXY_PORT = 8080;", f"const PROXY_PORT = {port};")
        with open(dynamic_config_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[{device_id}] Config generated for port {port}")

        try:
            def p(path): return os.path.abspath(os.path.join(BASE_PATH, path))
            frida_cmd = [
                FRIDA_BINARY, "-D", device_id,
                "-l", dynamic_config_path,
                "-l", p("Frida_hook/native-connect-hook.js"),
                "-l", p("Frida_hook/native-tls-hook.js"),
                "-l", p("Frida_hook/android/android-proxy-override.js"),
                "-l", p("Frida_hook/android/android-system-certificate-injection.js"),
                "-l", p("Frida_hook/android/android-certificate-unpinning.js"),
                "-l", p("Frida_hook/android/android-certificate-unpinning-fallback.js"),
                "-l", p("Frida_hook/android/android-disable-flutter-certificate-pinning.js"),
                "-l", p("Frida_hook/android/android-disable-root-detection.js"),
                "-f", package_id,
            ]
            logger.info(f"[{device_id}] Spawning {package_id}...")
            frida_proc = subprocess.Popen(frida_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            frida_monitor = FridaMonitor(frida_proc, device_id)
            frida_monitor.start()
            frida_monitor.crash_detected.wait(timeout=30)
            frida_monitor.check_crash()
            if frida_proc.poll() is not None:
                exit_code = frida_proc.returncode
                logger.warning(f"[{device_id}] Frida terminated prematurely (exit code: {exit_code})")
                raise FridaCrashError("STARTUP_CRASH", f"Frida process exited with code {exit_code}")

            # Checkpoint 3
            if health_check_callback:
                health_check_callback()

            if not adb_utils.android_has_internet(device_id):
                logger.warning(f"[{device_id}] [internet: KO]")
            else:
                logger.info(f"[{device_id}] [internet: OK]")

            return _run_analysis(device_id, package_id, screen_w, screen_h, health_check_callback, frida_monitor)

        except ChromeForegroundError:
            logger.warning(f"[{device_id}] Chrome detected in foreground, stopping analysis")
            return AnalysisResult.ERROR_CHROME
        except AppQuitError as e:
            logger.warning(f"[{device_id}] App quit during analysis: {e}")
            return AnalysisResult.APP_QUIT
        except Exception as e:
            logger.exception(f"[{device_id}] Exception: {e}")
            return f"ERROR_{e}"

    except ChromeForegroundError:
        logger.warning(f"[{device_id}] Chrome detected in foreground, stopping analysis")
        return AnalysisResult.ERROR_CHROME
    except AppQuitError as e:
        logger.warning(f"[{device_id}] App quit during analysis: {e}")
        return AnalysisResult.APP_QUIT
    except Exception as e:
        logger.error(f"[{device_id}] Error during analysis: {e}")
        return "ERROR_DURING_ANALYSIS"

    finally:
        tokens = utils_openai.get_token_count()
        logger.info(
            f"[{device_id}] [{package_id}] tokens OpenAI total — "
            f"input: {tokens['input']} | output: {tokens['output']} | "
            f"total: {tokens['input'] + tokens['output']}"
        )
        # 1. Signal voluntary shutdown before terminating Frida
        if frida_monitor:
            frida_monitor.set_terminating()
        # 2. Terminate Frida (communicate() to flush pipes and avoid deadlock)
        if frida_proc:
            frida_proc.terminate()
            try:
                frida_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{device_id}] Frida not responding, force killing...")
                frida_proc.kill()
                frida_proc.communicate(timeout=2)
            except Exception as e:
                logger.warning(f"[{device_id}] Error communicating with Frida: {e}")
                frida_proc.kill()
        # 3. Stop monitoring (pipes already closed by communicate)
        if frida_monitor:
            frida_monitor.stop()
        # 4. Stop the proxy
        if proxy_proc.is_alive():
            proxy_proc.terminate()
            proxy_proc.join(timeout=5)
            if proxy_proc.is_alive():
                logger.warning(f"[{device_id}] Proxy not responding to terminate, force killing...")
                proxy_proc.kill()
                proxy_proc.join(timeout=2)
        logger.info(f"[{device_id}] Proxy on port {port} closed.")
        time.sleep(1)
        har_file = log_file.replace(".jsonl", ".har")
        if os.path.exists(har_file) and os.path.getsize(har_file) > 0:
            logger.info(f"[{device_id}] HAR file available for worker: {har_file}")
        else:
            logger.info(f"[{device_id}] No HAR file generated (no network matches).")
        archive_file = log_file.replace(".jsonl", "_all.har")
        if os.path.exists(archive_file) and os.path.getsize(archive_file) > 0:
            logger.info(f"[{device_id}] Full archive available: {archive_file}")
        else:
            logger.info(f"[{device_id}] No requests archived (archive empty).")

def transfer_app(package, source, target):
    workdir = os.path.join("temp", source, package)
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)

    try:
        res = subprocess.check_output(
            f"adb -s {source} shell pm path {package}",
            shell=True,
            text=True
        ).strip()

        apk_paths = [l.replace("package:", "") for l in res.splitlines()]
        local_apks = []

        for p in apk_paths:
            dest = os.path.join(workdir, os.path.basename(p))
            subprocess.run(f"adb -s {source} pull {p} {dest}", shell=True, check=True)
            local_apks.append(dest)

        if len(local_apks) == 1:
            subprocess.run(f"adb -s {target} install -g {local_apks[0]}", shell=True, check=True)
        else:
            subprocess.run(
                f"adb -s {target} install-multiple -g {' '.join(local_apks)}",
                shell=True,
                check=True
            )

        return True
    except Exception as e:
        logger.error(f"[{source}] Transfer of {package} failed: {e}")
        return False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

if __name__ == "__main__":
    #transfer_app("deezer.android.app","emulator-5556","emulator-5554")
    #time.sleep(10)
    #print("Resetting Frida Server")
    #Database.truncate_all_tables()
    #adb_utils.reset_Frida_server("emulator-5554")
    #print("Frida Server reset")
    #time.sleep(5)
    #analyze_app("deezer.android.app","emulator-5554", 8080, health_check_callback=None)
    #print("start")
    #transfer_app("com.babbel.mobile.android.en","emulator-5556","emulator-5554")
    
    subprocess.run(
                    f"adb -s emulator-5554 shell settings put global http_proxy 10.0.2.2:8080",
                    shell=True,
                    check=True
                )
    log_file = os.path.abspath(os.path.join("temp", "emulator-5554", "capture.jsonl"))
    # 1. Start the Proxy in a separate process (more stable than threading for mitmproxy)
    # Use the port and log file specific to this emulator
    proxy_proc = multiprocessing.Process(
        target=start_proxy_process,
        args=(log_file, 8080)
    )
    proxy_proc.daemon = True
    proxy_proc.start()

    # Laisser le temps au proxy de s'ouvrir
    time.sleep(5000)


    #adb_utils.reset_Frida_server("emulator-5554")
    #analyze_app("com.babbel.mobile.android.en","emulator-5554", 8080, None)
    