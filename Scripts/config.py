"""
DEV / PROD configuration for the Android pipeline.

Launch:
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
parser.add_argument('--prod', action='store_true', help='Production mode (Ubuntu Server)')
parser.add_argument('--test', action='store_true', help='Test mode (test packages only)')
parser.add_argument('--packages-path', type=str, default=None, help='Root folder containing APKs ({path}/{package_id}/*.apk)')
parser.add_argument('--debug-proxy', action='store_true', help='Save all proxy requests to debug.json')
args, _ = parser.parse_known_args()

IS_WINDOWS = platform.system() == "Windows"
IS_PROD = args.prod or not IS_WINDOWS  # Auto-prod on Linux, unless explicitly Windows
IS_TEST = args.test and IS_PROD  # --test only works with --prod

if IS_TEST:
    MODE = "PROD-TEST"
elif IS_PROD:
    MODE = "PROD"
else:
    MODE = "DEV"

# ==========================================================
# ===================== TEST PACKAGES ======================
# ==========================================================
# Packages used in --prod --test mode
TEST_PACKAGES = [
    {"package_id": "com.bandsintown", "name": "Bandsintown"},
    {"package_id": "deezer.android.app", "name": "Deezer"},
]

# ==========================================================
# ===================== CONFIGURATION ======================
# ==========================================================

if IS_PROD or IS_TEST:
    # ============== PRODUCTION (Ubuntu Server) ==============
    AVD_MAPPING = {
        "emulator-5554": {"avd": "Root_1", "type": "ROOT"},
        "emulator-5556": {"avd": "Root_2", "type": "ROOT"},
        #"emulator-5554": {"avd": "Root_AOSP_1", "type": "ROOT"},
        #"emulator-5556": {"avd": "Root_2", "type": "ROOT"},
    }

    # Ubuntu paths - MODIFY according to your installation
    EMULATOR_BINARY = "/home/ubuntu/android-sdk/emulator/emulator"
    ADB_BINARY = "/home/ubuntu/android-sdk/platform-tools/adb"

    # Emulator options (headless for server)
    EMULATOR_LAUNCH_OPTS = (
        "-no-snapshot "
        "-dns-server 8.8.8.8 "
        "-no-window "
        "-no-audio "
    )

    # Delays (server may be slower)
    STABILIZATION_DELAY = 30
    POPUP_CHECK_DELAY = 15
    STARTUP_DELAY_BETWEEN_EMULATORS = 15

    # Root folder containing APKs: {PACKAGES_BASE_PATH}/{package_id}/*.apk
    PACKAGES_BASE_PATH = args.packages_path or os.getenv("PACKAGES_BASE_PATH", "/home/ubuntu/dev_osint/pipeline_osint/aurora_downloader/apks")

    # Rotating file logs (in the logs/ folder)
    LOG_TO_FILE = True
    LOG_DIR = "logs"
    LOG_FILE = os.path.join(LOG_DIR, "pipeline.log")
    LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
    LOG_BACKUP_COUNT = 5  # Keep 5 backup files

else:
    # ============== DEVELOPMENT (Windows) ==============
    AVD_MAPPING = {
        "emulator-5554": {"avd": "Root", "type": "ROOT"},
        #"emulator-5556": {"avd": "Root_2", "type": "ROOT"},
    }

    # Windows paths (in PATH)
    EMULATOR_BINARY = "emulator"
    ADB_BINARY = "adb"

    # Emulator options (with window for debug)
    EMULATOR_LAUNCH_OPTS = (
        "-no-snapshot "
        "-dns-server 8.8.8.8 "
        # Uncomment to hide window in DEV:
        #"-no-window "
        #"-no-audio "
    )

    # Delays
    STABILIZATION_DELAY = 30
    POPUP_CHECK_DELAY = 15
    STARTUP_DELAY_BETWEEN_EMULATORS = 15

    # Root folder containing APKs: {PACKAGES_BASE_PATH}/{package_id}/*.apk
    PACKAGES_BASE_PATH = args.packages_path or os.getenv("PACKAGES_BASE_PATH", r"C:\apks")

    # Console logs only
    LOG_TO_FILE = False
    LOG_DIR = None
    LOG_FILE = None
    LOG_MAX_SIZE = None
    LOG_BACKUP_COUNT = None

# ==========================================================
# ===================== COMMON CONFIGURATION ===============
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
    """Only passes log records containing the emulator serial."""
    def __init__(self, serial: str):
        super().__init__()
        self.serial = serial

    def filter(self, record: logging.LogRecord) -> bool:
        return self.serial in record.getMessage()


def setup_logging():
    """Configures logging according to the mode."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Log format
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Suppress HTTP logs from OpenAI SDK (httpx)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Console handler (always active)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Rotating file handler (PROD only)
    if LOG_TO_FILE and LOG_FILE:
        # Create the logs/ directory if needed
        if LOG_DIR:
            os.makedirs(LOG_DIR, exist_ok=True)

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_SIZE,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def setup_emulator_logger(serial: str) -> None:
    """
    Creates a dedicated log file for an emulator (logs/{serial}.log).
    Only records messages containing the serial.
    No effect if LOG_TO_FILE is False.
    """
    if not LOG_TO_FILE or not LOG_DIR:
        return

    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    serial_safe = serial.replace("-", "_")
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, f"{serial_safe}.log"),
        maxBytes=LOG_MAX_SIZE,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    handler.setFormatter(formatter)
    handler.addFilter(_EmulatorFilter(serial))
    logging.getLogger().addHandler(handler)


# ==========================================================
# ===================== CONFIG DISPLAY =====================
# ==========================================================
def print_config():
    """Displays the active configuration."""
    test_info = ""
    if IS_TEST:
        packages_list = ", ".join([p['package_id'] for p in TEST_PACKAGES])
        test_info = f"""
  TEST MODE ACTIVE
  Test packages: {packages_list}
{'='*60}"""

    print(f"""
{'='*60}
  MODE: {MODE}
  Platform: {'Windows' if IS_WINDOWS else 'Linux'}
{'='*60}
  ROOT emulators: {len([v for v in AVD_MAPPING.values() if v['type'] == 'ROOT'])}
  ADB: {ADB_BINARY}
  Emulator: {EMULATOR_BINARY}
  APKs path: {PACKAGES_BASE_PATH}
  File logging: {LOG_TO_FILE} {f'({LOG_FILE})' if LOG_TO_FILE else ''}
  Debug proxy: {DEBUG_PROXY}{test_info}
{'='*60}
""")
