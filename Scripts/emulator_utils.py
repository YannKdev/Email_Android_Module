"""
Android emulator management utilities.
Stateless functions extracted from main.py (no dependency on global variables).
"""
import subprocess
import time
import socket
import logging

import config

logger = logging.getLogger(__name__)
ADB_BINARY = config.ADB_BINARY


def is_device_online(serial: str) -> bool:
    """Checks whether an ADB device is online."""
    try:
        state = subprocess.check_output(
            f"{ADB_BINARY} -s {serial} get-state",
            shell=True,
            encoding='utf-8',
            errors='replace'
        ).strip()
        return state == "device"
    except subprocess.CalledProcessError:
        return False


def wait_for_boot(serial: str, timeout: int = 90) -> bool:
    """Waits for the emulator to finish booting (sys.boot_completed == 1)."""
    for _ in range(timeout):
        try:
            out = subprocess.check_output(
                f"{ADB_BINARY} -s {serial} shell getprop sys.boot_completed",
                shell=True,
                encoding='utf-8',
                errors='replace'
            ).strip()
            if out == "1":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def wait_for_tcp_port(host: str = "127.0.0.1", port: int = 5554, timeout: int = 30) -> bool:
    """Waits for a TCP port to be open."""
    start = time.time()
    while time.time() - start < timeout:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            time.sleep(1)
    return False


def ensure_root_environment(serial: str) -> bool:
    """Switches the emulator to root + disables SELinux (setenforce 0)."""
    try:
        out = subprocess.check_output(
            f"{ADB_BINARY} -s {serial} shell id",
            shell=True, encoding='utf-8', errors='replace'
        )
        if "uid=0(root)" not in out:
            for attempt in range(1, 6):
                try:
                    logger.info(f"[{serial}] adb root attempt {attempt}")
                    subprocess.run(
                        f"{ADB_BINARY} -s {serial} root",
                        shell=True, check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    subprocess.run(
                        f"{ADB_BINARY} -s {serial} wait-for-device",
                        shell=True, check=True, timeout=120
                    )
                    break
                except subprocess.CalledProcessError:
                    time.sleep(3)
            else:
                logger.error(f"[{serial}] Unable to switch to root mode")
                return False

        logger.info(f"[{serial}] setenforce 0")
        subprocess.run(
            f"{ADB_BINARY} -s {serial} shell setenforce 0",
            shell=True, check=False
        )
        return True
    except Exception as e:
        logger.error(f"[{serial}] Root environment error: {e}")
        return False


def wait_for_android_ready(serial: str, timeout: int = 60) -> bool:
    """Waits for Android to be ready (pm list packages responds)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            out = subprocess.check_output(
                f"{ADB_BINARY} -s {serial} shell pm list packages",
                shell=True, stderr=subprocess.DEVNULL, timeout=5
            )
            if out:
                return True
        except Exception:
            time.sleep(2)
    return False
