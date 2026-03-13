import subprocess
import os
from PIL import Image
from io import BytesIO
import re
import xml.etree.ElementTree as ET
import json
import time

# Import config pour le chemin ADB
try:
    import config
    ADB_BINARY = config.ADB_BINARY
except ImportError:
    ADB_BINARY = "adb"  # Fallback si config non disponible

def get_foreground_package(device_id: str) -> str:
    """
    Retourne le package name de l'app actuellement en premier plan.
    """
    try:
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "dumpsys", "activity", "activities"],
            capture_output=True,
            text=True,
            check=True
        )
        # Cherche le pattern "mResumedActivity" ou "topResumedActivity"
        for line in result.stdout.splitlines():
            if "mResumedActivity" in line or "topResumedActivity" in line:
                # Format: mResumedActivity: ActivityRecord{... com.package.name/.Activity ...}
                match = re.search(r'(\S+)/\.?\S*\s', line)
                if match:
                    return match.group(1)
        return ""
    except Exception:
        return ""

def is_chrome_foreground(device_id: str) -> bool:
    """
    Vérifie si Chrome est en premier plan.
    """
    fg_package = get_foreground_package(device_id)
    return "chrome" in fg_package.lower()

def start_app(serial, package_name):
    subprocess.run(
        f"{ADB_BINARY} -s {serial} shell monkey "
        f"-p {package_name} -c android.intent.category.LAUNCHER 1",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )
    #print(f"▶️ App lancée : {package_name} sur {serial}")

def uninstall_app(serial, package_name):
    subprocess.run(
        f"{ADB_BINARY} -s {serial} uninstall {package_name}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False   # certaines apps retournent un warning
    )
    #print(f"🗑 App désinstallée : {package_name} sur {serial}")


def uninstall_all_third_party_packages(serial):
    """
    Récupère la liste des packages tiers installés sur un device
    et les désinstalle tous.

    Args:
        serial (str): L'ID du device ou de l'émulateur (adb devices).

    Returns:
        list: Liste des packages désinstallés.
    """
    packages = get_installed_packages(serial)
    uninstalled = []
    total = len(packages)

    print(f"🧹 [{serial}] {total} packages tiers à désinstaller...")
    for i, package in enumerate(packages, 1):
        try:
            print(f"🗑️ [{serial}] ({i}/{total}) {package}")
            subprocess.run(
                f"{ADB_BINARY} -s {serial} uninstall {package}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30
            )
            uninstalled.append(package)
            time.sleep(2)  # Réduit de 5s à 2s
        except subprocess.TimeoutExpired:
            print(f"⚠️ [{serial}] Timeout désinstallation {package}, skip")
        except Exception as e:
            print(f"[ERROR] Impossible de désinstaller {package} : {e}")

    print(f"✅ [{serial}] {len(uninstalled)}/{total} packages désinstallés")
    return uninstalled


def stop_app(serial, package_name):
    subprocess.run(
        f"{ADB_BINARY} -s {serial} shell am force-stop {package_name}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )
    #print(f"⏹ App arrêtée : {package_name} sur {serial}")


_PROTECTED_PACKAGES = {
    "com.google.android.gms",
    "com.android.vending",
}

def get_installed_packages(serial, timeout=30):
    """
    Retourne l'ensemble des packages installés (apps tierces uniquement)
    sur l'émulateur / appareil identifié par son serial adb.
    Les packages dans _PROTECTED_PACKAGES (microG) sont exclus du résultat.
    """
    try:
        result = subprocess.check_output(
            f"{ADB_BINARY} -s {serial} shell pm list packages -3",
            shell=True,
            text=True,
            timeout=timeout
        )

        return {
            line.replace("package:", "").strip()
            for line in result.splitlines()
            if line.strip()
        } - _PROTECTED_PACKAGES
    except subprocess.TimeoutExpired:
        print(f"⚠️ [{serial}] Timeout sur pm list packages, retour liste vide")
        return set()
import subprocess
def adb_long_tap(x, y, device_id, duration_ms=2000):
    """
    Simule un tap long sur un appareil Android via ADB.
    
    Args:
        x (int): Coordonnée X du point à tap.
        y (int): Coordonnée Y du point à tap.
        duration_ms (int, optional): Durée du tap long en millisecondes. Default 2000.
        device_id (str, optional): ID de l'appareil pour adb -s. Default None.
    """
    cmd = [ADB_BINARY]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)]
    
    try:
        subprocess.run(cmd, check=True)
        #print(f"Tap long effectué en ({x},{y}) pour {duration_ms}ms sur {device_id or 'appareil par défaut'}")
    except subprocess.CalledProcessError as e:
        print(f"Erreur lors du tap long : {e}")
def disable_android_animations(device_id: str):
    """
    Désactive toutes les animations Android sur un device spécifique via ADB.
    
    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
    """
    commands = [
        [ADB_BINARY, "-s", device_id, "shell", "settings", "put", "global", "window_animation_scale", "0"],
        [ADB_BINARY, "-s", device_id, "shell", "settings", "put", "global", "transition_animation_scale", "0"],
        [ADB_BINARY, "-s", device_id, "shell", "settings", "put", "global", "animator_duration_scale", "0"]
    ]

    for cmd in commands:
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            #print(f"[OK] {' '.join(cmd)}")
            if result.stdout:
                print(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] {' '.join(cmd)}")
            if e.stderr:
                print(e.stderr.strip())

    print(f"✅ All animations disabled on {device_id}.")

def take_ui_xml(device_id: str, path=None):
    """
    Dump l'UI de l'appareil ciblé et la récupère localement.
    
    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices)
    """
    if(path == None):
        path = "temp/"+device_id+"/ui.xml"
    # Dump de l'UI sur le device
    subprocess.run(
        [ADB_BINARY, "-s", device_id, "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Récupération du fichier XML localement
    subprocess.run(
        [ADB_BINARY, "-s", device_id, "pull", "/sdcard/ui.xml", path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

#"""
def normalize_bounds(device_id, bounds_str):
    
    WIDTH, HEIGHT = get_emulator_size(device_id)
    left, top = map(int, bounds_str[1:-1].split('][')[0].split(','))
    right, bottom = map(int, bounds_str[1:-1].split('][')[1].split(','))
    x = round((left + right) / 2 / WIDTH, 3)
    y = round((top + bottom) / 2 / HEIGHT, 3)
    return x, y

_TYPE_PREFIXES = (
    "android.widget.", "android.view.", "android.webkit.",
    "androidx.", "com.google.android.material.",
)

def _short_type(full_type: str) -> str:
    if not full_type:
        return full_type
    for prefix in _TYPE_PREFIXES:
        if full_type.startswith(prefix):
            return full_type[len(prefix):]
    return full_type.rsplit(".", 1)[-1] if "." in full_type else full_type

def flatten_node(device_id, node, elements):
    node_type = node.attrib.get('class')
    text = node.attrib.get('text') or node.attrib.get('content-desc')
    clickable = node.attrib.get('clickable') == 'true'
    bounds = node.attrib.get('bounds')

    keep = (
        clickable
        or (node_type and 'EditText' in node_type)
        or (node_type and 'TextView' in node_type)
        or text
    )

    if keep and bounds:
        x, y = normalize_bounds(device_id, bounds)
        el = {
            "type": _short_type(node_type),
            "text": text,
            "clickable": clickable,
            "x": x,
            "y": y
        }
        # supprimer clés nulles
        el = {k: v for k, v in el.items() if v not in (None, "", False)}
        elements.append(el)

    for child in node:
        flatten_node(device_id, child, elements)

def deduplicate(elements):
    seen = set()
    unique = []
    for e in elements:
        key = (e.get("type"), e.get("text"), e.get("x"), e.get("y"))
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def clean_ui_xml(device_id:str, text_only:bool=False):
    """
    Convertit le XML UI en JSON.

    Args:
        device_id: ID du device
        text_only: Si True, garde uniquement les éléments avec text/content-desc non vide
                   (optimisation tokens pour appels sans screenshot)
    """
    input_path="temp/"+device_id+"/ui.xml"
    output_path="temp/"+device_id+"/ui.json"
    tree = ET.parse(input_path)
    root = tree.getroot()

    elements = []
    flatten_node(device_id, root, elements)
    elements = deduplicate(elements)

    # Filtrer pour ne garder que les éléments avec texte (optimisation tokens)
    # Les EditText sont toujours conservés même vides (champs de formulaire sans placeholder)
    if text_only:
        elements = [el for el in elements if el.get("text") or "EditText" in (el.get("type") or "") or el.get("clickable")]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"elements": elements}, f, ensure_ascii=False)




def adb_shell(cmd:str, device_id:str):
    subprocess.run(
        [ADB_BINARY, "-s", device_id, "shell", "su", "-c", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def reset_Frida_server(device_id: str) -> bool:
    """
    Vérifie si frida-server est en cours d'exécution, sinon le démarre.
    Retourne True si frida-server fonctionne, False sinon.
    """
    MAX_ATTEMPTS = 3

    def is_frida_running():
        """Vérifie si frida-server est en cours d'exécution."""
        result = subprocess.run(
            f'{ADB_BINARY} -s {device_id} shell "ps -A | grep frida-server"',
            capture_output=True,
            text=True,
            shell=True
        )
        output = result.stdout.strip()
        # Si on trouve "frida-server" dans la sortie (et pas juste "grep"), il tourne
        return "frida-server" in output and "grep" not in output

    def start_frida_server():
        """Tente de démarrer frida-server. Retourne (success, error_msg)."""
        # Lancer frida-server en arrière-plan avec nohup
        proc = subprocess.Popen(
            f'{ADB_BINARY} -s {device_id} shell "nohup /data/local/tmp/frida-server >/dev/null 2>&1 &"',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )
        try:
            stdout, stderr = proc.communicate(timeout=5)
            output = (stdout + stderr).strip()
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            # Timeout peut être normal si le serveur démarre
            return True, None

        # Si pas d'erreur, c'est bon
        if not output:
            return True, None

        # Erreur "Address already in use" - frida-server zombie
        if "Address already in use" in output:
            return False, "ADDRESS_IN_USE"

        # Autre erreur
        return False, output

    def kill_frida_server():
        """Kill frida-server."""
        subprocess.run(
            f'{ADB_BINARY} -s {device_id} shell "pkill frida-server"',
            capture_output=True,
            shell=True
        )
        time.sleep(2)

    # Toujours tuer frida-server avant de relancer pour repartir d'un état propre
    if is_frida_running():
        print(f"[Frida] frida-server en cours sur {device_id}, kill + restart...")
        kill_frida_server()

    # Boucle de tentatives
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[Frida] Tentative {attempt}/{MAX_ATTEMPTS} de démarrage sur {device_id}...")

        success, error = start_frida_server()

        if success:
            time.sleep(3)  # Laisser le temps au serveur de démarrer
            if is_frida_running():
                print(f"[Frida] ✅ frida-server démarré avec succès sur {device_id}")
                return True

        if error == "ADDRESS_IN_USE":
            print(f"[Frida] ⚠️ Port déjà utilisé, kill du processus zombie...")
            kill_frida_server()
            continue
        elif error:
            print(f"[Frida] ❌ Erreur: {error}")

        time.sleep(2)

    # Échec après toutes les tentatives
    print(f"[Frida] ❌ Impossible de démarrer frida-server sur {device_id} après {MAX_ATTEMPTS} tentatives")
    return False

def adb_scroll_half_screen(device_id=None, duration_ms=300):
    """
    Scroll vers le bas de la moitié de l'écran via ADB.
    
    Args:
        device_id (str, optional): ID de l'appareil. Default None.
        duration_ms (int): Durée du swipe en ms.
    """
    # Récupérer la résolution de l'écran
    cmd_size = [ADB_BINARY]
    if device_id:
        cmd_size += ["-s", device_id]
    cmd_size += ["shell", "wm", "size"]
    
    try:
        output = subprocess.check_output(cmd_size).decode()
        # Extrait largeur et hauteur : e.g. "Physical size: 1080x2400"
        size_str = output.strip().split(":")[1].strip()
        width, height = map(int, size_str.split("x"))
        
        # Coordonnées pour swipe
        x = width // 2
        y_start = height * 0.7  # 1/4 de l'écran (début du swipe)
        y_end = height * 0.3    # moitié de l'écran (fin du swipe)
        
        cmd_swipe = [ADB_BINARY]
        if device_id:
            cmd_swipe += ["-s", device_id]
        cmd_swipe += ["shell", "input", "swipe",
                      str(x), str(y_start), str(x), str(y_end), str(duration_ms)]
        
        subprocess.run(cmd_swipe, check=True)
        #print(f"Swipe vers le bas de la moitié de l'écran effectué sur {device_id or 'appareil par défaut'}")
        
    except subprocess.CalledProcessError as e:
        print(f"Erreur ADB : {e}")

def adb_back(device_id=None):
    """
    Simule le bouton 'Retour' sur un appareil Android via ADB.
    
    Args:
        device_id (str, optional): ID de l'appareil pour adb -s. Default None.
    """
    cmd = [ADB_BINARY]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "input", "keyevent", "KEYCODE_BACK"]
    
    try:
        subprocess.run(cmd, check=True)
        #print(f"Bouton retour envoyé sur {device_id or 'appareil par défaut'}")
    except subprocess.CalledProcessError as e:
        print(f"Erreur lors de l'envoi du bouton retour : {e}")
        
def take_snapshot(device_id :str, screenshot:bool=False, text_only:bool=False):
    """
    Prend un snapshot de l'UI (et optionnellement un screenshot).

    Args:
        device_id: ID du device
        screenshot: Si True, prend aussi un screenshot
        text_only: Si True, le JSON ne contiendra que les éléments avec texte
                   (optimisation tokens pour appels sans screenshot)
    """
    if(screenshot):
        take_android_screenshot(device_id=device_id)
    take_ui_xml(device_id=device_id)
    clean_ui_xml(device_id, text_only=text_only)

def take_android_screenshot(device_id: str):
    """
    Prend un screenshot d'un device Android spécifique et le sauvegarde en JPEG.
    Écrase le fichier s'il existe déjà.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
        file_path (str): Chemin complet du fichier JPEG à créer.
    """
    file_path = "temp/"+device_id+"/screenshot.jpeg"
    # S'assurer que le dossier existe
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # Commande adb pour capturer l'écran (PNG en sortie)
    adb_screencap_cmd = [ADB_BINARY, "-s", device_id, "exec-out", "screencap", "-p"]
    
    try:
        # Récupérer le screenshot en mémoire
        result = subprocess.run(adb_screencap_cmd, stdout=subprocess.PIPE, check=True)
        png_data = result.stdout
        
        # Convertir le PNG en JPEG
        image = Image.open(BytesIO(png_data))

        image = image.convert("RGB")
        image.save(
            file_path,
            "JPEG",
            quality=60,
            optimize=True,
            progressive=True
        )

        #print(f"✅ Screenshot de {device_id} sauvegardé dans : {file_path}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Erreur ADB lors de la capture sur {device_id} : {e}")
    except Exception as e:
        print(f"[ERROR] Erreur lors de la sauvegarde du screenshot : {e}")


def tap(device_id: str, x: int, y: int):
    """
    Simule un tap sur l'écran d'un device Android spécifique aux coordonnées (x, y).

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
        x (int): Coordonnée horizontale.
        y (int): Coordonnée verticale.
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "tap", str(x), str(y)],
            check=True
        )
        #print(f"✅ Tap effectué sur {device_id} en ({x}, {y})")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Erreur ADB sur {device_id} : impossible de faire le tap ({x}, {y}) - {e}")

def type_text(device_id: str, text: str):
    """
    Simule la saisie de texte sur un device Android spécifique.
    Gère les caractères spéciaux # et @.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
        text (str): Texte à saisir.
    """
    for char in text:
        try:
            if char == " ":
                # ADB remplace les espaces par %s
                subprocess.run(
                    [ADB_BINARY, "-s", device_id, "shell", "input", "text", "%s"],
                    check=True
                )
            elif char == "#":
                subprocess.run(
                    [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "18"],
                    check=True
                )
            elif char == "@":
                subprocess.run(
                    [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "77"],
                    check=True
                )
            else:
                subprocess.run(
                    [ADB_BINARY, "-s", device_id, "shell", "input", "text", char],
                    check=True
                )
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Erreur ADB sur {device_id} en tapant '{char}' : {e}")

def is_keyboard_active(device_id: str) -> bool:
    """
    Vérifie si le clavier virtuel est actuellement affiché sur le device.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).

    Returns:
        bool: True si le clavier est visible, False sinon.
    """
    try:
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "dumpsys", "input_method"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return "mInputShown=true" in result.stdout
    except Exception:
        return False


def hide_keyboard(device_id: str):
    """
    Cache le clavier virtuel sur un device Android spécifique si visible.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
    """
    try:
        # Tente d'envoyer KEYCODE_BACK pour fermer le clavier
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "4"],
            check=True
        )
        #print(f"✅ Clavier caché sur {device_id} (si visible)")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Erreur ADB sur {device_id} : impossible de cacher le clavier - {e}")

def send_tab(device_id: str):
    """
    Envoie la touche TAB sur un device Android spécifique.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "61"],
            check=True
        )
        # print(f"✅ Touche TAB envoyée sur {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Erreur ADB sur {device_id} : impossible d'envoyer TAB - {e}")
        
def press_enter(device_id: str):
    """
    Simule la touche Entrée sur un device Android spécifique.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "66"],
            check=True
        )
        #print(f"✅ Touche Entrée simulée sur {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Erreur ADB sur {device_id} : impossible de presser Entrée - {e}")

        
def dismiss_not_responding_popup(device_id: str) -> bool:
    """
    Vérifie si une popup "App not responding" est affichée et clique sur "Wait" si présente.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).

    Returns:
        bool: True si une popup a été détectée et fermée, False sinon.
    """
    try:
        # Méthode rapide : vérifier via dumpsys si une fenêtre ANR est affichée
        try:
            check_result = subprocess.run(
                [ADB_BINARY, "-s", device_id, "shell", "dumpsys", "window", "windows"],
                capture_output=True,
                text=True,
                timeout=5
            )
            has_anr_window = "Application Not Responding" in check_result.stdout or \
                             "AppNotResponding" in check_result.stdout or \
                             "isn't responding" in check_result.stdout.lower()
        except:
            has_anr_window = False

        # Dump l'UI sur le device (timeout augmenté car le système peut être lent)
        try:
            subprocess.run(
                [ADB_BINARY, "-s", device_id, "shell", "uiautomator", "dump", "/sdcard/ui_check.xml"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30  # Timeout augmenté à 30s car System UI peut être très lent
            )
        except subprocess.TimeoutExpired:
            # Si timeout et qu'on a détecté une fenêtre ANR, essayer de cliquer à une position connue
            if has_anr_window:
                print(f"⚠️ [{device_id}] uiautomator timeout mais ANR détecté, tentative de clic blind")
                # Position typique du bouton "Wait" sur la plupart des émulateurs
                subprocess.run(
                    [ADB_BINARY, "-s", device_id, "shell", "input", "tap", "360", "716"],
                    check=False,
                    timeout=5
                )
                time.sleep(0.5)
                return True
            raise

        # Récupérer le contenu du XML directement
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "cat", "/sdcard/ui_check.xml"],
            capture_output=True,
            text=True,
            timeout=15
        )

        xml_content = result.stdout.strip()
        if not xml_content:
            return False

        # Parser le XML
        root = ET.fromstring(xml_content)

        # Patterns pour détecter la popup "not responding" (multi-langue)
        not_responding_patterns = [
            "isn't responding",
            "isn't responding",  # variante apostrophe
            "is not responding",
            "not responding",
            "ne répond pas",
            "ne repond pas",
            "stopped responding",
            "has stopped",
        ]

        # Vérifier d'abord si c'est bien une popup "not responding"
        is_not_responding_popup = False
        for node in root.iter('node'):
            text = node.attrib.get('text', '').lower()
            resource_id = node.attrib.get('resource-id', '')
            # Chercher dans le titre ou n'importe quel texte
            if resource_id == 'android:id/alertTitle' or text:
                for pattern in not_responding_patterns:
                    if pattern.lower() in text:
                        is_not_responding_popup = True
                        break
            if is_not_responding_popup:
                break

        # Chercher le bouton à cliquer (priorité: Wait > Close app > OK)
        # Resource-ids possibles pour le bouton "Wait"
        wait_resource_ids = ['android:id/aerr_wait', 'android:id/button2']
        # Resource-ids possibles pour le bouton "Close" (fallback)
        close_resource_ids = ['android:id/aerr_close', 'android:id/button1']
        # Textes possibles pour les boutons
        wait_texts = ['wait', 'attendre', 'ok']
        close_texts = ['close', 'close app', 'fermer', 'ok']

        button_to_click = None

        # D'abord chercher par resource-id (plus fiable)
        for node in root.iter('node'):
            resource_id = node.attrib.get('resource-id', '')
            if resource_id in wait_resource_ids:
                button_to_click = node
                break

        # Si pas trouvé par resource-id, chercher par texte (si popup confirmée)
        if button_to_click is None and is_not_responding_popup:
            for node in root.iter('node'):
                text = node.attrib.get('text', '').lower()
                clickable = node.attrib.get('clickable', '') == 'true'
                if clickable and text:
                    for wait_text in wait_texts:
                        if wait_text == text:
                            button_to_click = node
                            break
                if button_to_click:
                    break

        # Fallback: chercher le bouton Close si Wait non trouvé (si popup confirmée)
        if button_to_click is None and is_not_responding_popup:
            for node in root.iter('node'):
                resource_id = node.attrib.get('resource-id', '')
                if resource_id in close_resource_ids:
                    button_to_click = node
                    break

            # Dernier recours: chercher Close par texte
            if button_to_click is None:
                for node in root.iter('node'):
                    text = node.attrib.get('text', '').lower()
                    clickable = node.attrib.get('clickable', '') == 'true'
                    if clickable and text:
                        for close_text in close_texts:
                            if close_text == text:
                                button_to_click = node
                                break
                    if button_to_click:
                        break

        if button_to_click is None:
            return False

        # Extraire les bounds et calculer le centre
        bounds = button_to_click.attrib.get('bounds', '')
        if not bounds:
            return False

        # Format: "[left,top][right,bottom]"
        match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if not match:
            return False

        left, top, right, bottom = map(int, match.groups())
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2

        # Cliquer sur le bouton
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "tap", str(center_x), str(center_y)],
            check=True,
            timeout=5
        )

        button_text = button_to_click.attrib.get('text', 'unknown')
        print(f"⚠️ [{device_id}] Popup 'not responding' détectée et fermée ('{button_text}' cliqué)")
        return True

    except subprocess.TimeoutExpired:
        print(f"⚠️ [{device_id}] Timeout lors de la vérification popup")
        return False
    except ET.ParseError:
        # XML invalide, pas de popup
        return False
    except Exception as e:
        print(f"⚠️ [{device_id}] Erreur vérification popup: {e}")
        return False


def get_emulator_size(device_id, adb_path="adb", retries=5, delay=3):
    """
    Retourne (width, height) de l'émulateur Android.
    Retente en cas d'erreur transitoire (ex: WM service pas encore prêt après un crash).
    """
    cmd = [adb_path]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "wm", "size"]

    last_error = None
    for attempt in range(retries):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            match = re.search(r"Physical size:\s*(\d+)x(\d+)", result.stdout)
            if not match:
                raise RuntimeError("Impossible de lire la résolution de l'émulateur")
            width, height = map(int, match.groups())
            return width, height
        except (subprocess.CalledProcessError, RuntimeError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(delay)

    raise RuntimeError(f"get_emulator_size échoué après {retries} tentatives: {last_error}")

def close_all_apps(device_id: str):
    """
    Retourne à l'écran d'accueil et tue toutes les apps en arrière-plan.
    À appeler entre deux analyses pour nettoyer les apps résiduelles.
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "KEYCODE_HOME"],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "am", "kill-all"],
            timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[{device_id}] Erreur close_all_apps: {e}")


def disable_airplane_mode_if_on(device_id: str) -> bool:
    """
    Vérifie si le mode avion est actif et le désactive si nécessaire.
    Retourne True si le mode avion était actif (et a été désactivé).
    """
    try:
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "settings", "get", "global", "airplane_mode_on"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == "1":
            print(f"[{device_id}] Mode avion détecté — désactivation...")
            subprocess.run(
                [ADB_BINARY, "-s", device_id, "shell", "settings", "put", "global", "airplane_mode_on", "0"],
                timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            subprocess.run(
                [ADB_BINARY, "-s", device_id, "shell", "am", "broadcast",
                 "-a", "android.intent.action.AIRPLANE_MODE", "--ez", "state", "false"],
                timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(3)
            print(f"[{device_id}] Mode avion désactivé")
            return True
        return False
    except Exception as e:
        print(f"[{device_id}] Erreur disable_airplane_mode_if_on: {e}")
        return False


def android_has_internet(device_id: str) -> bool:
    """
    Vérifie si l'émulateur Android a accès à Internet.

    Args:
        device_id (str): L'ID du device ou de l'émulateur (adb devices).

    Returns:
        bool: True si connecté à Internet, False sinon.
    """
    try:
        out = subprocess.check_output(
            [ADB_BINARY, "-s", device_id, "shell", "dumpsys", "connectivity"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return "NET_CAPABILITY_VALIDATED" in out or "VALIDATED" in out
    except Exception:
        return False