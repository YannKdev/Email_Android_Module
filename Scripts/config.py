"""
Configuration DEV / PROD pour le pipeline Android.

Lancement:
    - DEV:       python main_threading_2.py
    - PROD:      python main_threading_2.py --prod
    - PROD-TEST: python main_threading_2.py --prod --test
"""
import argparse
import platform
import logging
from logging.handlers import RotatingFileHandler
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# ===================== DETECTION MODE =====================
# ==========================================================
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--prod', action='store_true', help='Mode production (Ubuntu Server)')
parser.add_argument('--test', action='store_true', help='Mode test (packages de test uniquement)')
parser.add_argument('--packages-path', type=str, default=None, help='Dossier racine contenant les APKs ({path}/{package_id}/*.apk)')
parser.add_argument('--debug-proxy', action='store_true', help='Sauvegarde toutes les requêtes proxy dans debug.json')
args, _ = parser.parse_known_args()

IS_WINDOWS = platform.system() == "Windows"
IS_PROD = args.prod or not IS_WINDOWS  # Auto-prod sur Linux, sauf si explicitement Windows
IS_TEST = args.test and IS_PROD  # --test ne fonctionne qu'avec --prod

if IS_TEST:
    MODE = "PROD-TEST"
elif IS_PROD:
    MODE = "PROD"
else:
    MODE = "DEV"

# ==========================================================
# ===================== PACKAGES DE TEST ===================
# ==========================================================
# Packages utilises en mode --prod --test
TEST_PACKAGES = [
    {"package_id": "com.bandsintown", "name": "Bandsintown"},
    {"package_id": "deezer.android.app", "name": "Deezer"},
]

# ==========================================================
# ===================== CONFIGURATION ======================
# ==========================================================

if IS_PROD or IS_TEST:
    # ============== PRODUCTION (Ubuntu Server) ==============
    # AVDs Play Store rootés avec Magisk + Play Integrity Fix
    # Setup : rootAVD (https://gitlab.com/newbit/rootAVD) sur image google_apis_playstore x86_64
    AVD_MAPPING = {
        "emulator-5554": {"avd": "Magisk_1", "type": "MAGISK"},
        "emulator-5556": {"avd": "Magisk_2", "type": "MAGISK"},
    }

    # Chemins Ubuntu - A MODIFIER selon ton installation
    EMULATOR_BINARY = "/home/ubuntu/android-sdk/emulator/emulator"
    ADB_BINARY = "/home/ubuntu/android-sdk/platform-tools/adb"

    # Options emulateur (headless pour serveur)
    EMULATOR_LAUNCH_OPTS = (
        "-no-snapshot "
        "-dns-server 8.8.8.8 "
        "-no-window "
        "-no-audio "
    )

    # Delais (serveur peut etre plus lent)
    STABILIZATION_DELAY = 15
    POPUP_CHECK_DELAY = 5
    STARTUP_DELAY_BETWEEN_EMULATORS = 15

    # Dossier racine contenant les APKs : {PACKAGES_BASE_PATH}/{package_id}/*.apk
    PACKAGES_BASE_PATH = args.packages_path or os.getenv("PACKAGES_BASE_PATH", "/home/ubuntu/dev_osint/pipeline_osint/aurora_downloader/apks")

    # Logs en fichier rotatif (dans le dossier logs/)
    LOG_TO_FILE = True
    LOG_DIR = "logs"
    LOG_FILE = os.path.join(LOG_DIR, "pipeline.log")
    LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
    LOG_BACKUP_COUNT = 5  # Garde 5 fichiers de backup

else:
    # ============== DEVELOPPEMENT (Windows) ==============
    AVD_MAPPING = {
        "emulator-5554": {"avd": "Magisk_1", "type": "MAGISK"},
        "emulator-5556": {"avd": "Magisk_2", "type": "MAGISK"},
    }

    # Chemins Windows (dans le PATH)
    EMULATOR_BINARY = "emulator"
    ADB_BINARY = "adb"

    # Options emulateur (avec fenetre pour debug)
    EMULATOR_LAUNCH_OPTS = (
        "-no-snapshot "
        "-dns-server 8.8.8.8 "
        # Commenter pour voir la fenetre en DEV:
        #"-no-window "
        #"-no-audio "
    )

    # Delais
    STABILIZATION_DELAY = 15
    POPUP_CHECK_DELAY = 5
    STARTUP_DELAY_BETWEEN_EMULATORS = 15

    # Dossier racine contenant les APKs : {PACKAGES_BASE_PATH}/{package_id}/*.apk
    PACKAGES_BASE_PATH = args.packages_path or os.getenv("PACKAGES_BASE_PATH", r"C:\apks")

    # Logs console uniquement
    LOG_TO_FILE = False
    LOG_DIR = None
    LOG_FILE = None
    LOG_MAX_SIZE = None
    LOG_BACKUP_COUNT = None

# ==========================================================
# ===================== CONFIGURATION COMMUNE ==============
# ==========================================================
WATCHDOG_INTERVAL = 30
TRANSFER_QUEUE_MAXSIZE = 5
ROOT_PORT_START = 8080
DEBUG_PROXY = args.debug_proxy
DEBUG_PROXY_FILE = os.path.join(os.path.abspath("."), "debug.json")
if DEBUG_PROXY and not os.path.exists(DEBUG_PROXY_FILE):
    import json
    with open(DEBUG_PROXY_FILE, "w") as _f:
        json.dump([], _f)

# ==========================================================
# ===================== LOGGING SETUP ======================
# ==========================================================
class _EmulatorFilter(logging.Filter):
    """Ne laisse passer que les records contenant le serial de l'émulateur."""
    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial

    def filter(self, record: logging.LogRecord) -> bool:
        return self.serial in record.getMessage()


class _CleanFormatter(logging.Formatter):
    """Formatter unifié : datetime uniquement au premier message, emojis pour WARNING/ERROR, pas de [INFO]."""
    _first_logged = False

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info:
            msg = msg + '\n' + self.formatException(record.exc_info)

        if record.levelno >= logging.ERROR:
            prefix = "❌ "
        elif record.levelno >= logging.WARNING:
            prefix = "⚠️  "
        else:
            prefix = ""

        if not _CleanFormatter._first_logged:
            _CleanFormatter._first_logged = True
            ts = self.formatTime(record, '%Y-%m-%d %H:%M:%S')
            return f"{ts}  {prefix}{msg}"

        return f"{prefix}{msg}"


def setup_logging():
    """Configure le logging selon le mode."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Supprimer les logs HTTP du SDK OpenAI (httpx)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Supprimer les warnings TLS de Google Play Services (certificate pinning attendu)
    logging.getLogger("mitmproxy.proxy.layers.tls").setLevel(logging.ERROR)

    # Handler console (toujours actif)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_CleanFormatter())
    logger.addHandler(console_handler)

    # Handler fichier rotatif (PROD uniquement)
    if LOG_TO_FILE and LOG_FILE:
        if LOG_DIR:
            os.makedirs(LOG_DIR, exist_ok=True)

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_SIZE,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setFormatter(_CleanFormatter())
        logger.addHandler(file_handler)

    return logger


def setup_emulator_logger(serial: str) -> None:
    """
    Crée un fichier log dédié à un émulateur (logs/{serial}.log).
    N'enregistre que les messages contenant le serial.
    Sans effet si LOG_TO_FILE est False.
    """
    if not LOG_TO_FILE or not LOG_DIR:
        return

    os.makedirs(LOG_DIR, exist_ok=True)

    serial_safe = serial.replace("-", "_")
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, f"{serial_safe}.log"),
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    handler.setFormatter(_CleanFormatter())
    handler.addFilter(_EmulatorFilter(serial))
    logging.getLogger().addHandler(handler)


# ==========================================================
# ===================== AFFICHAGE CONFIG ===================
# ==========================================================
def print_config():
    """Affiche la configuration active."""
    test_info = ""
    if IS_TEST:
        packages_list = ", ".join([p['package_id'] for p in TEST_PACKAGES])
        test_info = f"""
  MODE TEST ACTIF
  Packages de test: {packages_list}
{'='*60}"""

    print(f"""
{'='*60}
  MODE: {MODE}
  Plateforme: {'Windows' if IS_WINDOWS else 'Linux'}
{'='*60}
  Emulateurs ROOT: {len([v for v in AVD_MAPPING.values() if v['type'] == 'ROOT'])}
  ADB: {ADB_BINARY}
  Emulator: {EMULATOR_BINARY}
  APKs path: {PACKAGES_BASE_PATH}
  Logs fichier: {LOG_TO_FILE} {f'({LOG_FILE})' if LOG_TO_FILE else ''}
  Debug proxy: {DEBUG_PROXY}{test_info}
{'='*60}
""")
