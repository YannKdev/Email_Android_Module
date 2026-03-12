"""
Script one-shot : installe microG (GmsCore + FakeStore) + frida-server
sur les émulateurs AVDs. À exécuter UNE SEULE FOIS par AVD.

microG est installé comme app utilisateur protégée (_PROTECTED_PACKAGES).
frida-server est pushé dans /data/local/tmp/ et persiste dans userdata.

Usage :
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
# Supporte fichier direct ou dossier contenant le binaire
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
    print(f"  [{serial}] Attente boot...")
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
    print(f"\n[{serial}] Installation microG (app utilisateur)...")

    for apk_file, pkg_dir in APKS:
        apk_path = os.path.join(MICROG_DIR, apk_file)
        if not os.path.isfile(apk_path):
            print(f"  [{serial}] SKIP : {apk_path} introuvable")
            continue

        print(f"  [{serial}] install {apk_file}...")
        res = run(f'{ADB} -s {serial} install -g -r "{apk_path}"', timeout=60, check=False)
        if res.returncode == 0 and "Success" in res.stdout:
            print(f"  [{serial}] ✓ {pkg_dir} installé")
        else:
            print(f"  [{serial}] ✗ {pkg_dir} échec : {res.stdout.strip()} {res.stderr.strip()}")
            return False

    # Vérification
    print(f"  [{serial}] Vérification...")
    pm_out = run(f"{ADB} -s {serial} shell pm list packages", timeout=10, check=False).stdout
    installed = set(line.replace("package:", "").strip() for line in pm_out.splitlines())
    for apk_file, pkg_dir in APKS:
        # Déduire le package name depuis le nom de fichier (tout ce qui précède le premier tiret-version)
        pkg_name = "com.google.android.gms" if "gms" in apk_file else "com.android.vending"
        if pkg_name in installed:
            print(f"  [{serial}] ✓ {pkg_name} présent")
        else:
            print(f"  [{serial}] ✗ {pkg_name} NON trouvé")

    # Push frida-server
    if os.path.isfile(FRIDA_SERVER_PATH):
        print(f"  [{serial}] Push frida-server...")
        res = run(f'{ADB} -s {serial} push "{FRIDA_SERVER_PATH}" /data/local/tmp/frida-server', timeout=30, check=False)
        if res.returncode == 0:
            run(f"{ADB} -s {serial} shell chmod 755 /data/local/tmp/frida-server", timeout=10, check=False)
            print(f"  [{serial}] ✓ frida-server pushé")
        else:
            print(f"  [{serial}] ✗ frida-server échec : {res.stderr.strip()}")
    else:
        print(f"  [{serial}] SKIP frida-server : {FRIDA_SERVER_PATH} introuvable")

    return True


def start_emulator(avd_name: str, port: int) -> None:
    cmd = f"{EMULATOR} -avd {avd_name} -port {port} {LAUNCH_OPTS}"
    print(f"  Démarrage AVD '{avd_name}' sur port {port}...")
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    serial = f"emulator-{port}"
    subprocess.run(f"{ADB} -s {serial} wait-for-device", shell=True, timeout=120)
    wait_boot(serial, timeout=120)


def main():
    parser = argparse.ArgumentParser(description="Setup one-shot microG sur les AVDs")
    parser.add_argument("--serials", nargs="+",
                        help="Serials spécifiques (ex: emulator-5554 emulator-5556). "
                             "Par défaut : tous les AVDs dans AVD_MAPPING.")
    args = parser.parse_args()

    mapping = config.AVD_MAPPING
    if args.serials:
        mapping = {s: mapping[s] for s in args.serials if s in mapping}

    if not mapping:
        print("Aucun émulateur trouvé dans AVD_MAPPING.")
        sys.exit(1)

    # Vérifier que les APKs existent
    print(f"APKs microG dans : {MICROG_DIR}")
    for apk_file, _ in APKS:
        path = os.path.join(MICROG_DIR, apk_file)
        status = "✓" if os.path.isfile(path) else "✗ MANQUANT"
        print(f"  {status}  {apk_file}")
    print()

    for serial, info in mapping.items():
        avd_name = info["avd"]
        port = int(serial.split("-")[-1])

        # Démarrer l'émulateur si pas en ligne
        res = subprocess.run(
            f"{ADB} -s {serial} get-state",
            shell=True, capture_output=True, text=True
        )
        if res.returncode != 0 or res.stdout.strip() != "device":
            start_emulator(avd_name, port)
            time.sleep(5)

        install_microg(serial)

    print("\nSetup terminé. Les apps microG sont installées en apps système et persisteront.")
    print("Tu peux relancer le pipeline normalement.")


if __name__ == "__main__":
    main()
