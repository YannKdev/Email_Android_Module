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

# Chemin absolu vers le binaire frida du venv courant
# Fonctionne aussi bien en session interactive qu'avec nohup
FRIDA_BINARY = os.path.join(os.path.dirname(sys.executable), "frida")

logger = logging.getLogger(__name__)


def wait_for_tcp_port(host="127.0.0.1", port=8080, timeout=30):
    """Attend que le port TCP soit ouvert (proxy prêt)."""
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
    """Exception levée quand Frida détecte un crash du processus."""
    def __init__(self, error_type, message):
        self.error_type = error_type  # Ex: "TRACE_BPT_TRAP", "BAD_ACCESS"
        self.message = message
        super().__init__(f"FRIDA_ERROR_{error_type}: {message}")

class ChromeForegroundError(Exception):
    """Exception levée quand Chrome est détecté en premier plan."""
    pass

class AppQuitError(Exception):
    """Exception levée quand l'app analysée n'est plus en premier plan."""
    pass


def check_foreground(device_id: str, package_id: str):
    """
    Vérifie que l'app analysée est bien en premier plan.
    Lève ChromeForegroundError si Chrome est détecté.
    Si une autre app est au premier plan, tente d'abord un appui sur Back :
      - si l'app revient au premier plan → continue normalement
      - sinon → lève AppQuitError
    Ne fait rien si le foreground ne peut pas être déterminé.
    """
    fg = adb_utils.get_foreground_package(device_id)
    if not fg:
        return
    if "chrome" in fg.lower():
        raise ChromeForegroundError(f"Chrome détecté en premier plan sur {device_id}")
    if package_id and fg != package_id:
        logger.info(f"[{device_id}] App {package_id} pas au premier plan ({fg}), tentative Back...")
        adb_utils.adb_back(device_id)
        time.sleep(1.5)
        fg2 = adb_utils.get_foreground_package(device_id)
        if fg2 and fg2 == package_id:
            logger.info(f"[{device_id}] App {package_id} revenue au premier plan après Back, continuation.")
            return
        raise AppQuitError(f"[{device_id}] App {package_id} quittée — premier plan après Back: {fg2 or fg}")


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
    ERROR_NO_INTERNET           = "ERROR_NO_INTERNET"


def check_chrome_foreground(device_id: str):
    """Vérifie si Chrome est en premier plan. Lève ChromeForegroundError si oui."""
    if adb_utils.is_chrome_foreground(device_id):
        raise ChromeForegroundError(f"Chrome détecté en premier plan sur {device_id}")


# Patterns pour détecter les erreurs Frida
FRIDA_CRASH_PATTERNS = {
    "TRACE_BPT_TRAP": r"Process crashed: Trace/BPT trap",
    "BAD_ACCESS": r"Process crashed: Bad access",
    "SEGFAULT": r"Process crashed: Segmentation fault",
    "SIGABRT": r"Process crashed: Aborted",
    "SIGKILL": r"Process crashed: Killed",
    "PROCESS_TERMINATED": r"Process terminated",
    "FRIDA_SERVER_NOT_RUNNING": r"Failed to spawn|Unable to connect to remote frida-server",
    # Nouveaux patterns pour erreurs Java/Native
    "UNSATISFIED_LINK": r"Process crashed:.*UnsatisfiedLinkError",
    "FATAL_EXCEPTION": r"FATAL EXCEPTION:",
    # "FRIDA_SESSION_ENDED" retiré : "Thank you for using Frida!" est un message de sortie normale, pas un crash
    "FRIDA_PYTHON_ERROR": r"Fatal Python error:(?!.*_enter_buffered_busy.*interpreter shutdown)",
    "DLOPEN_FAILED": r"dlopen failed:",
    "STARTUP_ERROR": r"Error logged during startup",
    "APP_CRASH": r"Process crashed:",
}


class FridaMonitor:
    """Monitore la sortie de Frida pour détecter les crashes."""

    def __init__(self, process, device_id):
        self.process = process
        self.device_id = device_id
        self.crash_detected = threading.Event()
        self.crash_error = None
        self.play_store_detected = threading.Event()
        self._stop_event = threading.Event()
        self._terminating = False  # True quand on arrête volontairement Frida
        self._monitor_thread = None
        self._stderr_thread = None
        self._stdout_thread = None

    def set_terminating(self):
        """Signale que Frida va être arrêté volontairement — désactive la détection de crash."""
        self._terminating = True

    def start(self):
        """Démarre le monitoring en background avec des threads séparés pour stdout/stderr."""
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
        """Arrête le monitoring."""
        self._stop_event.set()
        # Fermer les streams pour débloquer les readline()
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
        """Vérifie si un crash a été détecté. Lève FridaCrashError si oui."""
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
        """Lit un stream (stdout ou stderr) ligne par ligne."""
        try:
            while not self._stop_event.is_set():
                try:
                    line = stream.readline()
                    if not line:
                        # Stream fermé ou EOF
                        break
                    line_str = line.decode('utf-8', errors='ignore').strip()
                    if line_str:
                        if any(noise in line_str for noise in self.TLS_NOISE):
                            continue
                        if not self._terminating:
                            logger.info(f"[Frida {self.device_id}] {line_str}")
                        if "[FRIDA_PLAY_STORE_REQUIRED]" in line_str:
                            if not self.play_store_detected.is_set():
                                logger.info(f"[{self.device_id}] Play Store redirect intercepté par Frida")
                            self.play_store_detected.set()
                        self._check_for_crash(line_str)
                except (ValueError, OSError):
                    # Stream fermé pendant la lecture
                    break
        except Exception as e:
            if not self._stop_event.is_set():
                logger.warning(f"[FridaMonitor {self.device_id}] Erreur {stream_name}: {e}")

    def _check_for_crash(self, line):
        """Vérifie si la ligne contient une erreur de crash."""
        if self._terminating or self.crash_detected.is_set():
            return
        for error_type, pattern in FRIDA_CRASH_PATTERNS.items():
            if re.search(pattern, line, re.IGNORECASE):
                if error_type == "PROCESS_TERMINATED":
                    logger.info(f"[Frida {self.device_id}] App s'est fermée (PROCESS_TERMINATED)")
                else:
                    logger.error(f"💥 [Frida {self.device_id}] CRASH DÉTECTÉ: {error_type}")
                self.crash_error = FridaCrashError(error_type, line)
                self.crash_detected.set()
                return

class SimpleCaptureAddon:
    # Termes à rechercher dans les requêtes (email encodé ou non)
    SEARCH_TERMS = [
        "test@gmail.com",
        "test%40gmail.com",
        "dGVzdEBnbWFpbC5jb20=",            # base64
        "1aedb8d9dc4751e229a335e371db8058",  # MD5
        "87924606b4131a8aceeeae8868531fbb9712aaa07a5d3a756b26ce0f5d6ca674",  # SHA256
    ]

    def __init__(self, output_file):
        # Le fichier sera .har au lieu de .jsonl
        self.output_file = output_file.replace(".jsonl", ".har")
        self.entries = []
        self.start_time = time.time()
        self.debug_entries = []
        self.debug_file = DEBUG_PROXY_FILE if DEBUG_PROXY else None
        # Archive inconditionnelle de toutes les requêtes (debug)
        self.archive_file = self.output_file.replace(".har", "_all.har")
        self.archive_entries = []

    def _create_har_entry(self, flow):
        """Convertit un flow mitmproxy en entrée HAR standard."""
        import datetime

        # Headers en format HAR
        req_headers = [{"name": k, "value": v} for k, v in flow.request.headers.items()]
        res_headers = [{"name": k, "value": v} for k, v in flow.response.headers.items()]

        # Corps de la requête
        req_body_text = ""
        try:
            req_body_text = flow.request.text if flow.request.text else ""
        except:
            req_body_text = ""

        # Corps de la réponse
        res_body_text = ""
        try:
            res_body_text = flow.response.text if flow.response.text else ""
        except:
            res_body_text = ""

        # Calcul des timings
        started = datetime.datetime.fromtimestamp(flow.request.timestamp_start).isoformat() + "Z"
        total_time = (flow.response.timestamp_end - flow.request.timestamp_start) * 1000  # en ms

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
        """Vérifie si le flow contient les termes recherchés."""
        # Vérifier dans l'URL
        url = flow.request.pretty_url
        if any(term in url for term in self.SEARCH_TERMS):
            return True

        # Vérifier dans le corps de la requête
        try:
            req_body = flow.request.text or ""
            if any(term in req_body for term in self.SEARCH_TERMS):
                return True
        except:
            pass

        # Vérifier dans le corps de la réponse
        try:
            res_body = flow.response.text or ""
            if any(term in res_body for term in self.SEARCH_TERMS):
                return True
        except:
            pass

        return False

    def http_connect(self, flow):
        # Debug : logger toutes les tentatives CONNECT (avant TLS, avant request)
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
        # Appelé quand le handshake TLS échoue côté client (app Android → mitmproxy)
        import datetime
        try:
            addr = tls_start.context.server.address
            host = addr[0] if addr else "unknown"
            port = addr[1] if addr else 0
            error = str(tls_start.error) if getattr(tls_start, "error", None) else "TLS handshake failed"

            # Archiver l'échec TLS dans capture_all.har
            tls_entry = {
                "startedDateTime": datetime.datetime.utcnow().isoformat() + "Z",
                "time": 0,
                "_tls_failed": True,
                "_tls_error": error,
                "request": {
                    "method": "CONNECT",
                    "url": f"https://{host}:{port}",
                    "httpVersion": "unknown",
                    "headers": [],
                    "queryString": [],
                    "postData": {"mimeType": "", "text": ""},
                    "headersSize": -1,
                    "bodySize": 0,
                },
                "response": {
                    "status": 0,
                    "statusText": f"TLS_FAILED: {error}",
                    "httpVersion": "unknown",
                    "headers": [],
                    "content": {"size": 0, "mimeType": "", "text": ""},
                    "redirectURL": "",
                    "headersSize": -1,
                    "bodySize": 0,
                },
                "cache": {},
                "timings": {"send": 0, "wait": 0, "receive": 0},
            }
            self.archive_entries.append(tls_entry)
            self._save_archive_file()
        except Exception:
            pass

        if not self.debug_file:
            return
        try:
            addr = tls_start.context.server.address
            host = addr[0] if addr else "unknown"
            port = addr[1] if addr else 0
            error = str(tls_start.error) if getattr(tls_start, "error", None) else "TLS handshake failed"
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
        # Appelé quand le handshake TLS réussit côté client
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
        # Debug : logger toutes les requêtes entrantes (TLS réussi)
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

        # Debug : enrichir l'entrée request() existante avec la réponse
        if self.debug_file:
            res_body_preview = ""
            try:
                raw = flow.response.text or ""
                res_body_preview = raw[:500] + ("…" if len(raw) > 500 else "")
            except Exception:
                pass
            has_email = self._matches_search_terms(flow)
            # Trouver et mettre à jour l'entrée request correspondante
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
                # Pas d'entrée request préalable (ex: réponse sans request loggé)
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

        # Archive inconditionnelle de toutes les requêtes (pour debug)
        archive_entry = self._create_har_entry(flow)
        self.archive_entries.append(archive_entry)
        self._save_archive_file()

        # Filtrage du bruit Google
        if any(x in url for x in ["accounts.google.com", "fonts.gstatic.com"]):
            return

        # Ne stocker QUE si ça matche les termes recherchés
        if not self._matches_search_terms(flow):
            return

        logger.info(f"[HAR CAPTURED - MATCH] {url}")

        # Créer l'entrée HAR et l'ajouter à la liste
        har_entry = self._create_har_entry(flow)
        self.entries.append(har_entry)

        # Sauvegarder le fichier HAR complet à chaque nouvelle entrée
        self._save_har_file()

    def _save_har_file(self):
        """Sauvegarde le fichier HAR complet."""
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
        """Sauvegarde l'archive complète de toutes les requêtes (sans filtre)."""
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
        # On passe le chemin spécifique au fichier pour cet émulateur/package
        master.addons.add(SimpleCaptureAddon(output_path))
        logger.info(f"Proxy mitmproxy démarré sur port {port}")
    except Exception as e:
        logger.error(f"Erreur démarrage proxy sur port {port}: {e}")
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
    """Tape sur un champ texte uniquement si le clavier n'est pas déjà visible."""
    if not adb_utils.is_keyboard_active(device_id):
        adb_utils.tap(device_id, x=x, y=y)
        time.sleep(5)


def _tap_submit(device_id: str, resp: dict, screen_w: int, screen_h: int) -> bool:
    """Appuie sur le bouton submit s'il existe. Retourne True si cliqué."""
    if resp["submit_button"] == "NO_SUBMIT_BUTTON":
        return False
    adb_utils.tap(
        device_id,
        x=screen_w * resp["submit_button"]["x"],
        y=screen_h * resp["submit_button"]["y"],
    )
    time.sleep(15)
    return True


# ---------------------------------------------------------------------------
# Navigation steps
# ---------------------------------------------------------------------------

def _check_play_store_popup(device_id: str) -> bool:
    """
    Vérifie si un popup "Play Store requis" est affiché (bouton CLOSE détecté).
    Prend un snapshot et lit le ui.json nettoyé.
    Retourne True si le popup est détecté.
    """
    adb_utils.take_snapshot(device_id, screenshot=False, text_only=False)
    ui_path = os.path.join("temp", device_id, "ui.json")
    try:
        with open(ui_path, "r", encoding="utf-8") as f:
            ui_data = json.load(f)
    except Exception as e:
        logger.warning(f"[{device_id}] Impossible de lire ui.json pour vérif Play Store: {e}")
        return False
    for el in ui_data.get("elements", []):
        if (
            el.get("type") == "android.widget.Button"
            and el.get("text") == "CLOSE"
            and el.get("clickable") is True
        ):
            logger.info(f"[{device_id}] Popup Play Store requis détecté (bouton CLOSE trouvé).")
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
    Navigue vers la page de login.
    Retourne None si trouvée, sinon un AnalysisResult d'erreur.
    """
    logger.info(f"[{device_id}] Début navigation vers login (max {max_attempts} tentatives)")
    already_tapped: list = []
    previous_hash = ""
    same_screen_count = 0
    for it in range(1, max_attempts + 1):
        if health_check_callback:
            health_check_callback()
        if frida_monitor:
            frida_monitor.check_crash()
            if frida_monitor.play_store_detected.is_set():
                return AnalysisResult.PLAY_STORE_REQUIRED
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
        current_hash = _ui_hash(device_id)
        if current_hash and current_hash == previous_hash:
            same_screen_count += 1
        else:
            same_screen_count = 0
        previous_hash = current_hash

        if same_screen_count >= 3:
            logger.warning(f"[{device_id}] UI inchangé {same_screen_count} fois de suite → FAILED_GO_TO_LOGIN — {it}/{max_attempts}")
            return AnalysisResult.FAILED_GO_TO_LOGIN

        if resp["etat"] == "NEED_SCREENSHOT":
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

        if resp["etat"] in ("MODALS", "OTHER"):
            where_tap = resp.get("where_tap")
            if not where_tap:
                logger.warning(f"[{device_id}] {resp['etat']} sans where_tap — {it}/{max_attempts}")
                time.sleep(2)
                continue
            if where_tap.get("action") == "BACK":
                logger.info(f"[{device_id}] BACK pour {resp['etat']} — {it}/{max_attempts}")
                adb_utils.adb_back(device_id)
            else:
                tap_x = screen_w * where_tap["x"]
                tap_y = screen_h * where_tap["y"]
                logger.info(f"[{device_id}] Tap ({tap_x:.0f}, {tap_y:.0f}) pour {resp['etat']} — {it}/{max_attempts}")
                adb_utils.tap(device_id, x=tap_x, y=tap_y)
                already_tapped.append({
                    "name": where_tap.get("name", ""),
                    "x": where_tap["x"],
                    "y": where_tap["y"],
                })
            time.sleep(3)
        elif resp["etat"] in ("NO_LOGIN", "NO_EMAIL_LOGIN"):
            logger.info(f"[{device_id}] {resp['etat']} — pas de login email — {it}/{max_attempts}")
            return AnalysisResult.NO_LOGIN
        elif resp["etat"] == "LOGIN_EMAIL":
            logger.info(f"[{device_id}] Page login trouvée — {it}/{max_attempts}")
            return None
        else:
            logger.warning(f"[{device_id}] État non géré: {resp['etat']} — {it}/{max_attempts}")
    logger.warning(f"[{device_id}] Max tentatives atteint sans trouver login")
    return AnalysisResult.FAILED_GO_TO_LOGIN


def _navigate_to_register(
    device_id: str, screen_w: int, screen_h: int, package_id: str, max_attempts: int,
) -> bool:
    """Navigue vers la page register. Retourne True si trouvée."""
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
    time.sleep(5)

    # Si une capture HAR a déjà été générée par l'Enter, pas besoin de cliquer submit
    har_path = os.path.join("temp", device_id, "capture.har")
    if os.path.exists(har_path) and os.path.getsize(har_path) > 0:
        logger.info(f"[{device_id}] HAR capturé après Enter — pas de clic submit nécessaire.")
        return AnalysisResult.END_EMAIL_UNIQUE_NO_SUBMIT

    # Pas de HAR : cacher le clavier, re-demander à l'IA les coords fraîches du bouton submit
    adb_utils.hide_keyboard(device_id)
    time.sleep(2)
    check_foreground(device_id, package_id)
    resp_fresh = utils_openai.analyze_login_page(device_id, package_name=package_id)
    if _tap_submit(device_id, resp_fresh, screen_w, screen_h):
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
    package_id: str,
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
    time.sleep(1)
    submitted = _tap_submit(device_id, resp, screen_w, screen_h)
    logger.info(
        f"[{device_id}] {'Submitted fake email+password' if submitted else 'No submit button (Email + Password)'}."
    )
    return AnalysisResult.END_EMAIL_MDP_OK


# ---------------------------------------------------------------------------
# Analysis orchestrator
# ---------------------------------------------------------------------------

def _run_analysis(
    device_id: str, package_id: str, screen_w: int, screen_h: int,
    health_check_callback, frida_monitor,
) -> AnalysisResult:
    """Logique pure d'analyse UI — navigation, login."""
    MAX_LOGIN_ATTEMPTS = 8

    if _check_play_store_popup(device_id):
        return AnalysisResult.PLAY_STORE_REQUIRED
    if frida_monitor and frida_monitor.play_store_detected.is_set():
        logger.info(f"[{device_id}] Play Store redirect détecté par Frida avant navigation")
        return AnalysisResult.PLAY_STORE_REQUIRED

    nav_result = _navigate_to_login(
        device_id, screen_w, screen_h, package_id,
        MAX_LOGIN_ATTEMPTS, health_check_callback, frida_monitor,
    )
    if nav_result == AnalysisResult.FAILED_GO_TO_LOGIN:
        logger.info(f"[{device_id}] Login introuvable après {MAX_LOGIN_ATTEMPTS} tentatives.")
        return AnalysisResult.NO_LOGIN
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
        return _handle_email_mdp(device_id, resp, screen_w, screen_h, package_id)
    if resp["etat"] == "NO_LOGIN":
        logger.info(f"[{device_id}] No login page found.")
        return AnalysisResult.NO_LOGIN
    return AnalysisResult.UNKNOWN_ENDING


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_app(package_id, device_id, port, health_check_callback=None):
    """
    Lance le proxy sur un port dédié, effectue l'analyse ADB et ferme tout.

    Args:
        package_id: Package de l'app à analyser
        device_id: Serial de l'émulateur
        port: Port du proxy
        health_check_callback: Fonction optionnelle qui vérifie la santé de l'émulateur.
                              Doit lever une exception si l'émulateur est offline.

    Returns:
        AnalysisResult (str enum)

    Raises:
        RuntimeError: Si l'émulateur devient offline pendant l'analyse
        InterruptedError: Si un arrêt global est demandé
    """
    utils_openai.reset_token_counter()

    if health_check_callback:
        health_check_callback()

    log_file = os.path.abspath(os.path.join("temp", device_id, "capture.jsonl"))
    proxy_proc = multiprocessing.Process(target=start_proxy_process, args=(log_file, port))
    proxy_proc.daemon = True
    proxy_proc.start()

    if not wait_for_tcp_port(port=port, timeout=30):
        logger.warning(f"[{device_id}] Timeout proxy port {port}, on continue quand même...")

    ADB_BINARY = "adb"
    subprocess.run(
        f"{ADB_BINARY} -s {device_id} shell settings put global http_proxy :0",
        shell=True, check=True, timeout=10,
    )
    subprocess.run(
        f"{ADB_BINARY} -s {device_id} shell settings put global http_proxy 10.0.2.2:{port}",
        shell=True, check=True, timeout=10,
    )

    adb_utils.reset_Frida_server(device_id)

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

        # Génération de la config Frida spécifique au port de ce worker
        dynamic_config_path = os.path.abspath(os.path.join("temp", device_id, "config_root.js"))
        template_path = os.path.join(BASE_PATH, "Frida_hook/config.js")
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("const PROXY_PORT = 8080;", f"const PROXY_PORT = {port};")
        with open(dynamic_config_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[{device_id}] ✅ Configuration (Frida+proxy) : OK")

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
            logger.info(f"[{device_id}] 🚀 Spawning {package_id}...")
            frida_proc = subprocess.Popen(frida_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            frida_monitor = FridaMonitor(frida_proc, device_id)
            frida_monitor.start()
            frida_monitor.crash_detected.wait(timeout=30)
            frida_monitor.check_crash()
            if frida_proc.poll() is not None:
                exit_code = frida_proc.returncode
                logger.warning(f"[{device_id}] Frida a terminé prématurément (exit code: {exit_code})")
                raise FridaCrashError("STARTUP_CRASH", f"Frida process exited with code {exit_code}")

            # Checkpoint 3
            if health_check_callback:
                health_check_callback()

            if not adb_utils.android_has_internet(device_id):
                logger.warning(f"[{device_id}] [internet: KO] → arrêt analyse")
                return AnalysisResult.ERROR_NO_INTERNET
            else:
                logger.info(f"[{device_id}] [internet: OK]")

            return _run_analysis(device_id, package_id, screen_w, screen_h, health_check_callback, frida_monitor)

        except ChromeForegroundError:
            logger.warning(f"[{device_id}] Chrome détecté en premier plan, arrêt de l'analyse")
            return AnalysisResult.ERROR_CHROME
        except AppQuitError as e:
            logger.warning(f"[{device_id}] App quittée pendant l'analyse: {e}")
            return AnalysisResult.APP_QUIT
        except Exception as e:
            logger.exception(f"[{device_id}] Exception: {e}")
            return f"ERROR_{e}"

    except ChromeForegroundError:
        logger.warning(f"[{device_id}] Chrome détecté en premier plan, arrêt de l'analyse")
        return AnalysisResult.ERROR_CHROME
    except AppQuitError as e:
        logger.warning(f"[{device_id}] App quittée pendant l'analyse: {e}")
        return AnalysisResult.APP_QUIT
    except Exception as e:
        logger.error(f"[{device_id}] Erreur durant l'analyse : {e}")
        return "ERROR_DURING_ANALYSIS"

    finally:
        tokens = utils_openai.get_token_count()
        logger.info(
            f"[{device_id}] [{package_id}] tokens OpenAI total — "
            f"input: {tokens['input']} | output: {tokens['output']} | "
            f"total: {tokens['input'] + tokens['output']}"
        )
        # 1. Signaler l'arrêt volontaire avant de terminer Frida
        if frida_monitor:
            frida_monitor.set_terminating()
        # 2. Terminer Frida (communicate() pour vider les pipes et éviter deadlock)
        if frida_proc:
            frida_proc.terminate()
            try:
                frida_proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{device_id}] Frida ne répond pas, kill forcé...")
                frida_proc.kill()
                frida_proc.communicate(timeout=2)
            except Exception as e:
                logger.warning(f"[{device_id}] Erreur communicate Frida: {e}")
                frida_proc.kill()
        # 3. Arrêter le monitoring (pipes déjà fermées par communicate)
        if frida_monitor:
            frida_monitor.stop()
        # 4. Arrêter le proxy
        if proxy_proc.is_alive():
            proxy_proc.terminate()
            proxy_proc.join(timeout=5)
            if proxy_proc.is_alive():
                logger.warning(f"[{device_id}] Proxy ne répond pas au terminate, kill forcé...")
                proxy_proc.kill()
                proxy_proc.join(timeout=2)
        time.sleep(1)

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
        logger.error(f"[{source}] Transfert {package} échoué : {e}")
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
    # 1. Démarrage du Proxy dans un processus séparé (plus stable que threading pour mitmproxy)
    # On utilise le port et le fichier de log spécifiques à cet émulateur
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
    