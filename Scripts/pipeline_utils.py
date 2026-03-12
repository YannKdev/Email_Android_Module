"""
Utilitaires pipeline : nettoyage processus, transfert APK, analyse avec timeout.
Fonctions stateless extraites de main.py (pas de dépendance aux variables globales).
"""
import subprocess
import os
import shutil
import time
import socket
import multiprocessing
import queue
import logging

import config
import Analyze_proxy
from Analyze_proxy import FridaCrashError

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)
ADB_BINARY = config.ADB_BINARY


class PackageServiceDeadError(Exception):
    """Levée quand le service Android Package Manager ne répond plus (zombie emulator)."""
    pass


def kill_process_on_port(port: int, device_id: str = "") -> bool:
    """
    Tue tout processus en écoute sur un port spécifique (Linux).
    Utilise fuser pour identifier les PIDs puis les kill.
    """
    try:
        result = subprocess.run(
            f"fuser {port}/tcp",
            shell=True, capture_output=True, text=True, timeout=10
        )
        pids = result.stdout.strip().split()
        if not pids:
            return False
        killed = False
        for pid in pids:
            pid = pid.strip()
            if pid.isdigit():
                logger.info(f"[{device_id}] Kill PID {pid} sur port {port}")
                subprocess.run(
                    f"kill -9 {pid}", shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
                )
                killed = True
        if killed:
            time.sleep(1)
        return killed
    except Exception as e:
        logger.warning(f"Erreur kill_process_on_port: {e}")
    return False


def cleanup_orphan_processes(proxy_port: int, device_id: str) -> None:
    """
    Nettoie les processus orphelins (mitmproxy, frida) liés à CE worker.
    Vérifie les patterns de port spécifiques (listen_port=, port=) pour éviter
    de tuer des processus Python non liés.
    """
    killed_count = 0

    if kill_process_on_port(proxy_port, device_id):
        killed_count += 1

    if not PSUTIL_AVAILABLE:
        return

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = (proc.info['name'] or '').lower()
            cmdline = proc.info['cmdline'] or []
            cmdline_str = ' '.join(cmdline).lower()

            # Mitmproxy/mitmdump orphelin — patterns stricts pour éviter les faux positifs
            if 'mitmdump' in name or 'mitmproxy' in name or 'python' in name:
                port_patterns = [
                    f'listen_port={proxy_port}',
                    f'port={proxy_port}',
                ]
                if any(pattern in cmdline_str for pattern in port_patterns):
                    logger.info(f"[{device_id}] Kill mitmproxy orphelin PID {proc.pid} (port {proxy_port})")
                    proc.kill()
                    killed_count += 1
                    continue

            # Frida CLI orphelin pour CE device (pas frida-server)
            if 'frida' in name and 'server' not in name:
                if device_id.lower() in cmdline_str:
                    logger.info(f"[{device_id}] Kill frida CLI orphelin PID {proc.pid}")
                    proc.kill()
                    killed_count += 1
                    continue

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        except Exception as e:
            logger.warning(f"Erreur cleanup process: {e}")

    if killed_count > 0:
        logger.info(f"[{device_id}] {killed_count} processus orphelin(s) nettoyé(s)")
        time.sleep(1)


def check_host_internet_connectivity(timeout: int = 5) -> bool:
    """Vérifie que l'hôte a accès à Internet via DNS Google/Cloudflare."""
    test_hosts = [("8.8.8.8", 53), ("1.1.1.1", 53)]
    for host, port in test_hosts:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        except (socket.error, socket.timeout):
            continue
    return False


def restore_host_connectivity(device_id: str, proxy_port: int) -> bool:
    """Tente de restaurer la connectivité de l'hôte en nettoyant les processus proxy."""
    logger.info(f"[{device_id}] Tentative de restauration de la connectivité...")
    kill_process_on_port(proxy_port, device_id)
    cleanup_orphan_processes(proxy_port, device_id)
    time.sleep(3)
    if check_host_internet_connectivity():
        logger.info(f"[{device_id}] Connectivité restaurée")
        return True
    logger.error(f"[{device_id}] Connectivité toujours KO après nettoyage")
    return False


def transfer_app(package: str, source: str, target: str) -> bool:
    """Transfère un APK depuis l'émulateur PS vers l'émulateur Root via pull/install."""
    workdir = os.path.join("temp", source, package)
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    try:
        res = subprocess.check_output(
            f"{ADB_BINARY} -s {source} shell pm path {package}",
            shell=True, encoding='utf-8', errors='replace'
        ).strip()

        apk_paths = [line.replace("package:", "") for line in res.splitlines()]
        local_apks = []

        for p in apk_paths:
            dest = os.path.join(workdir, os.path.basename(p))
            subprocess.run(f"{ADB_BINARY} -s {source} pull {p} {dest}", shell=True, check=True)
            local_apks.append(dest)

        if len(local_apks) == 1:
            subprocess.run(f"{ADB_BINARY} -s {target} install -g {local_apks[0]}", shell=True, check=True)
        else:
            subprocess.run(
                f"{ADB_BINARY} -s {target} install-multiple -g {' '.join(local_apks)}",
                shell=True, check=True
            )
        return True
    except Exception as e:
        logger.error(f"Transfert {package} échoué : {e}")
        return False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def install_from_local(package_id: str, base_path: str, device_id: str) -> bool:
    """
    Installe les APKs d'un package depuis le dossier local vers l'émulateur.
    Cherche tous les fichiers .apk dans {base_path}/{package_id}/.

    Args:
        package_id: Identifiant du package (ex: "com.whatsapp")
        base_path: Dossier racine contenant les APKs
        device_id: Serial de l'émulateur cible

    Returns:
        True si installation réussie, False sinon
    """
    apk_dir = os.path.join(base_path, package_id)
    if not os.path.isdir(apk_dir):
        logger.error(f"[{device_id}] Dossier APK introuvable: {apk_dir}")
        return False

    apks = sorted([
        os.path.join(apk_dir, f)
        for f in os.listdir(apk_dir)
        if f.lower().endswith('.apk')
    ])
    if not apks:
        logger.error(f"[{device_id}] Aucun fichier .apk dans {apk_dir}")
        return False

    logger.info(f"[{device_id}] Installation de {package_id} ({len(apks)} APK(s)) depuis {apk_dir}")
    try:
        if len(apks) == 1:
            subprocess.run(
                f'{ADB_BINARY} -s {device_id} install -g "{apks[0]}"',
                shell=True, check=True, timeout=120,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        else:
            apks_str = ' '.join(f'"{a}"' for a in apks)
            subprocess.run(
                f'{ADB_BINARY} -s {device_id} install-multiple -g {apks_str}',
                shell=True, check=True, timeout=120,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        logger.info(f"[{device_id}] Installation de {package_id} OK")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('utf-8', errors='replace') if e.stderr else ''
        logger.error(f"[{device_id}] Échec installation {package_id}: {stderr.strip()}")
        if "Can't find service: package" in stderr:
            raise PackageServiceDeadError(
                f"[{device_id}] Service package Android mort (zombie) — redémarrage nécessaire"
            )
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[{device_id}] Timeout installation {package_id}")
        return False
    except Exception as e:
        logger.error(f"[{device_id}] Erreur installation {package_id}: {e}")
        return False


def _analyze_worker(package: str, device_id: str, port: int, result_queue) -> None:
    """Worker function picklable pour l'analyse d'app via multiprocessing."""
    try:
        res = Analyze_proxy.analyze_app(package, device_id=device_id, port=port, health_check_callback=None)
        result_queue.put({"type": "result", "value": res})
    except FridaCrashError as e:
        result_queue.put({"type": "frida_crash", "error_type": e.error_type, "message": e.message})
    except Exception as e:
        result_queue.put({"type": "error", "value": f"ERROR: {e}"})


def analyze_app_with_timeout(package: str, device_id: str, port: int, timeout: int = 360, health_check=None) -> str:
    """
    Analyse une app avec timeout via multiprocessing.
    Vérifie le health_check toutes les 2s pendant l'attente (fix émulateur offline).
    """
    result_queue = multiprocessing.Queue()
    p = multiprocessing.Process(
        target=_analyze_worker,
        args=(package, device_id, port, result_queue)
    )
    p.start()

    deadline = time.time() + timeout
    while p.is_alive():
        if time.time() >= deadline:
            p.terminate()
            p.join(timeout=5)
            logger.warning(f"Timeout ({timeout}s) : {package} sur {device_id}")
            cleanup_orphan_processes(port, device_id)
            return "TIMEOUT"
        if health_check:
            try:
                health_check()
            except (RuntimeError, InterruptedError):
                p.terminate()
                p.join(timeout=5)
                if p.is_alive():
                    p.kill()
                raise  # Re-raise pour que root_worker gère la remise en attente
        p.join(timeout=2)

    try:
        result = result_queue.get(timeout=10)
    except queue.Empty:
        logger.warning(f"Queue vide après analyse de {package} sur {device_id}")
        return "ERROR_QUEUE_EMPTY"

    if result["type"] == "frida_crash":
        raise FridaCrashError(result["error_type"], result["message"])
    if result["type"] == "error":
        return result["value"]
    return result["value"]
