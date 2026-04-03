from pathlib import Path
import subprocess

VPS_USER = "ubuntu"
VPS_HOST = "your.vps.ip"

SSH_KEY = Path("/path/to/your/ssh_key")

EMULATOR_SERIAL = "emulator-5554"
LOCAL_OUTPUT_PATH = Path("./SSH_Tools/Screenshot/emulator_screen.png")
ADB_PATH = "/home/ubuntu/android-sdk/platform-tools/adb"
SSH_OPTS = [
    "-i", str(SSH_KEY),
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null"
]

ssh_cmd = f"{ADB_PATH} -s emulator-5554 exec-out screencap -p"

with open(LOCAL_OUTPUT_PATH, "wb") as f:
    subprocess.run(
        [
            "ssh",
            "-i", str(SSH_KEY),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{VPS_USER}@{VPS_HOST}",
            ssh_cmd
        ],
        stdout=f,
        check=True
    )
print("✅ Screenshot retrieved successfully")
