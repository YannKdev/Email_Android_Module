"""
live_view.py — Updates temp/{device_id}/live.jpeg every 2 seconds.
Direct visualisation from VS Code via SSH (open the file in the editor).
"""

import logging
import os
import subprocess
import threading
from io import BytesIO

logger = logging.getLogger(__name__)

ADB_BINARY = "adb"


class _Capturer:
    def __init__(self, device_id: str, interval: float = 2.0):
        self._device_id = device_id
        self._interval  = interval
        self._stop      = threading.Event()
        self._thread    = threading.Thread(
            target=self._run,
            name=f"live-cap-{device_id}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        from PIL import Image

        out_path = os.path.join("temp", self._device_id, "live.jpeg")
        while not self._stop.wait(self._interval):
            try:
                result = subprocess.run(
                    [ADB_BINARY, "-s", self._device_id, "exec-out", "screencap", "-p"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                if result.returncode != 0 or not result.stdout:
                    continue
                img = Image.open(BytesIO(result.stdout)).convert("RGB")
                img.save(out_path, "JPEG", quality=50, optimize=True)
            except Exception:
                pass  # Device not ready yet — retry on next tick


class LiveViewServer:
    def __init__(self, devices: list, **_):
        self._capturers = [_Capturer(d) for d in devices]

    def start(self):
        for c in self._capturers:
            c.start()
        logger.info("Live capture started — files: temp/<device>/live.jpeg")

    def stop(self):
        for c in self._capturers:
            c.stop()
