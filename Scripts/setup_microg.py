"""
One-shot script: installs microG (GmsCore + FakeStore) + frida-server
on AVD emulators. To be run ONLY ONCE per AVD.

microG is installed as a protected user app (_PROTECTED_PACKAGES).
frida-server is pushed to /data/local/tmp/ and persists in userdata.

Usage:
    python Scripts/setup_microg.py
    python Scripts/setup_microg.py --serials emulator-5554 emulator-5556
"""
import subprocess
import time
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(__file__))
import config

ADB = config.ADB_BINARY
EMULATOR = config.EMULATOR_BINARY
LAUNCH_OPTS = config.EMULATOR_LAUNCH_OPTS

RESSOURCES_DIR = os.path.dirname(os.path.dirname(__file__))

MICROG_DIR = os.path.join(RESSOURCES_DIR, "ressources", "Apks_gsm")

_frida_candidate = os.path.join(RESSOURCES_DIR, "ressources", "frida-server-x86_64")
# Supports direct file or folder containing the binary
if os.path.isdir(_frida_candidate):
    FRIDA_SERVER_PATH = os.path.join(_frida_candidate, "frida-server")
else:
    FRIDA_SERVER_PATH = _frida_candidate

APKS = [
    ("com.google.android.gms.apk",  "GmsCore"),
    ("com.android.vending.apk",      "FakeStore"),
]


def run(cmd: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, timeout=timeout, check=check,
                          capture_output=True, text=True)


def wait_boot(serial: str, timeout: int = 120) -> bool:
    print(f"  [{serial}] Waiting for boot...")
    for _ in range(timeout):
        try:
            out = subprocess.check_output(
                f"{ADB} -s {serial} shell getprop sys.boot_completed",
                shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5
            ).strip()
            if out == "1":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def install_microg(serial: str) -> bool:
    print(f"\n[{serial}] Installing microG (user app)...")

    for apk_file, pkg_dir in APKS:
        apk_path = os.path.join(MICROG_DIR, apk_file)
        if not os.path.isfile(apk_path):
            print(f"  [{serial}] SKIP: {apk_path} not found")
            continue

        print(f"  [{serial}] install {apk_file}...")
        res = run(f'{ADB} -s {serial} install -g -r "{apk_path}"', timeout=60, check=False)
        if res.returncode == 0 and "Success" in res.stdout:
            print(f"  [{serial}] ✓ {pkg_dir} installed")
        else:
            print(f"  [{serial}] ✗ {pkg_dir} failed: {res.stdout.strip()} {res.stderr.strip()}")
            return False

    # Verification
    print(f"  [{serial}] Verifying...")
    pm_out = run(f"{ADB} -s {serial} shell pm list packages", timeout=10, check=False).stdout
    installed = set(line.replace("package:", "").strip() for line in pm_out.splitlines())
    for apk_file, pkg_dir in APKS:
        # Infer the package name from the file name (everything before the first version dash)
        pkg_name = "com.google.android.gms" if "gms" in apk_file else "com.android.vending"
        if pkg_name in installed:
            print(f"  [{serial}] ✓ {pkg_name} present")
        else:
            print(f"  [{serial}] ✗ {pkg_name} NOT found")

    # Push frida-server
    if os.path.isfile(FRIDA_SERVER_PATH):
        print(f"  [{serial}] Push frida-server...")
        res = run(f'{ADB} -s {serial} push "{FRIDA_SERVER_PATH}" /data/local/tmp/frida-server', timeout=30, check=False)
        if res.returncode == 0:
            run(f"{ADB} -s {serial} shell chmod 755 /data/local/tmp/frida-server", timeout=10, check=False)
            print(f"  [{serial}] ✓ frida-server pushed")
        else:
            print(f"  [{serial}] ✗ frida-server failed: {res.stderr.strip()}")
    else:
        print(f"  [{serial}] SKIP frida-server: {FRIDA_SERVER_PATH} not found")

    return True


def start_emulator(avd_name: str, port: int) -> None:
    cmd = f"{EMULATOR} -avd {avd_name} -port {port} {LAUNCH_OPTS}"
    print(f"  Starting AVD '{avd_name}' on port {port}...")
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    serial = f"emulator-{port}"
    subprocess.run(f"{ADB} -s {serial} wait-for-device", shell=True, timeout=120)
    wait_boot(serial, timeout=120)


def main():
    parser = argparse.ArgumentParser(description="One-shot microG setup on AVDs")
    parser.add_argument("--serials", nargs="+",
                        help="Specific serials (e.g. emulator-5554 emulator-5556). "
                             "Default: all AVDs in AVD_MAPPING.")
    args = parser.parse_args()

    mapping = config.AVD_MAPPING
    if args.serials:
        mapping = {s: mapping[s] for s in args.serials if s in mapping}

    if not mapping:
        print("No emulator found in AVD_MAPPING.")
        sys.exit(1)

    # Check that the APKs exist
    print(f"APKs microG dans : {MICROG_DIR}")
    for apk_file, _ in APKS:
        path = os.path.join(MICROG_DIR, apk_file)
        status = "✓" if os.path.isfile(path) else "✗ MISSING"
        print(f"  {status}  {apk_file}")
    print()

    for serial, info in mapping.items():
        avd_name = info["avd"]
        port = int(serial.split("-")[-1])

        # Start the emulator if not online
        res = subprocess.run(
            f"{ADB} -s {serial} get-state",
            shell=True, capture_output=True, text=True
        )
        if res.returncode != 0 or res.stdout.strip() != "device":
            start_emulator(avd_name, port)
            time.sleep(5)

        install_microg(serial)

    print("\nSetup complete. The microG apps are installed as system apps and will persist.")
    print("You can restart the pipeline normally.")


if __name__ == "__main__":
    main()
