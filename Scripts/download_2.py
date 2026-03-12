import subprocess
import time
import os
import adb_utils
import re
import xml.etree.ElementTree as ET
import PS_verif
import logging

logger = logging.getLogger(__name__)

# Dict par device_id pour éviter les collisions en multithreading (Fix 4)
_has_found_barrier: dict = {}


# --- EXCEPTIONS ---
class AgeVerificationError(Exception):
    """Exception levée quand le Play Store demande une vérification d'âge."""
    def __init__(self, package_id, message="Age verification required by Play Store"):
        self.package_id = package_id
        self.message = message
        super().__init__(f"ERROR_AGE_PLAY_STORE: {package_id} - {message}")


class CountryNotAvailableError(Exception):
    """Exception levée quand l'app n'est pas disponible dans le pays."""
    def __init__(self, package_id, message="App not available in this country"):
        self.package_id = package_id
        self.message = message
        super().__init__(f"ERROR_COUNTRY: {package_id} - {message}")


class VersionNotCompatibleError(Exception):
    """Exception levée quand l'app n'est pas compatible avec la version du device."""
    def __init__(self, package_id, message="Device not compatible with this version"):
        self.package_id = package_id
        self.message = message
        super().__init__(f"ERROR_VERSION: {package_id} - {message}")
def updateUITree(device_id):
    adb_utils.take_ui_xml(device_id, os.path.join("temp", device_id, "Download", "ui.xml"))
      #adb_utils.clean_ui_xml("temp/Download/ui.xml", "temp/Download/ui.json")

def reset_open_play_store(device_id):
    _has_found_barrier[device_id] = False
    
    # 1. Forcer l'arrêt du Play Store
    subprocess.run(["adb", "-s", device_id, "shell", "am", "force-stop", "com.android.vending"], check=False)
    time.sleep(3)
    
    # 2. Relancer proprement
    cmd = [
        "adb", "-s", device_id,
        "shell", "am", "start",
        "-n", "com.android.vending/com.google.android.finsky.activities.MainActivity"
    ]
    subprocess.run(cmd, check=False)
    time.sleep(10)


def find_node_by_text(device_id:str, text: str):
    """
    Recherche un node dont le texte contient 'text' et retourne ses coordonnées.

    :param xml_path: chemin vers ui.xml
    :param text: texte à rechercher partiellement (ex: "Search")
    :return: dict avec bounds ou None si non trouvé
    """
    xml_path = os.path.join("temp", device_id, "Download", "ui.xml")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    text_lower = text.lower()

    for node in root.iter("node"):
        node_text = node.attrib.get("text", "")
        if text_lower in node_text.lower():  # contains insensible à la casse
            bounds = node.attrib.get("bounds")
            if not bounds:
                return None

            # Extraction des coordonnées depuis "[x1,y1][x2,y2]"
            x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))

            return {
                "center_x": (x1 + x2) // 2,
                "center_y": (y1 + y2) // 2
            }

    return None


def parse_bounds(bounds_str):
    """
    Convertit un string '[x1,y1][x2,y2]' en centre (cx, cy)
    """
    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if not match:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    return (cx, cy)

def get_nodes_with_newline(device_id:str):
    xml_file = os.path.join("temp", device_id, "Download", "ui.xml")
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    results = []
    
    for node in root.iter():
        content_desc = node.attrib.get('content-desc', '')
        bounds = node.attrib.get('bounds', '')
        
        # Cherche le vrai saut de ligne '\n', pas '&#10'
        if '\n' in content_desc:
            center = parse_bounds(bounds)
            if center:
                results.append({
                    'content-desc': content_desc,
                    'center': center
                })
    return results

def content_desc_contains(device_id, text):
    """
    Vérifie si un content-desc contient le texte donné dans tout le XML.
    
    Args:
        xml_file (str): Chemin du fichier XML.
        text (str): Texte à rechercher.
        
    Returns:
        bool: True si au moins un content-desc contient le texte, False sinon.
    """
    xml_file = os.path.join("temp", device_id, "Download", "ui.xml")
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    for node in root.iter():
        content_desc = node.attrib.get('content-desc', '')
        if text in content_desc:
            return True
    return False

def get_last_node_position(device_id, text):
    """
    Retourne le centre (x, y) du dernier node dont le content-desc contient le texte donné.
    
    Args:
        xml_file (str): Chemin du fichier XML.
        text (str): Texte à rechercher dans le content-desc.
    
    Returns:
        tuple: (x, y) du centre du node trouvé, ou None si aucun node ne correspond.
    """
    xml_file = os.path.join("temp", device_id, "Download", "ui.xml")
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    last_center = None
    
    for node in root.iter():
        content_desc = node.attrib.get('content-desc', '')
        bounds = node.attrib.get('bounds', '')
        
        if content_desc == text:
            center = parse_bounds(bounds)
            if center:
                last_center = center  # on garde toujours le dernier trouvé
    
    return last_center

def has_exact_text(device_id, text):
    """
    Vérifie si le XML contient un node dont le content-desc est exactement égal au texte donné.
    
    Args:
        xml_file (str): Chemin du fichier XML.
        text (str): Texte exact à rechercher.
    
    Returns:
        bool: True si un node correspond, False sinon.
    """
    xml_file = os.path.join("temp", device_id, "Download", "ui.xml")
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    for node in root.iter():
        content_desc = node.attrib.get('content-desc', '')
        if content_desc == text:
            return True
    return False


def valid_position(device_id, y):
    related_node = find_node_by_text(device_id, "Related to your search")
    limited_node = find_node_by_text(device_id, "Limited-time events")
    more_result_node = find_node_by_text(device_id, "More results")
    max_y = None
    if related_node is not None:
        max_y = related_node["center_y"]
        _has_found_barrier[device_id] = True
    if limited_node is not None:
        _has_found_barrier[device_id] = True
        if max_y is None:
            max_y = limited_node["center_y"]
        else:
            max_y = max(max_y, limited_node["center_y"])
    if more_result_node is not None:
        _has_found_barrier[device_id] = False
        return y > more_result_node["center_y"]
    if max_y is None and not _has_found_barrier.get(device_id, False):
        return True
    return False

#adb_scroll_half_screen(emulator_manager.emulator_PS)

#updateUITree()
#time.sleep(20)
#updateUITree()
#time.sleep(20)
def find_app_name_from_star_rating(xml_path):
    """
    Retourne la première ligne du content-desc qui contient 'Star rating:'.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    for elem in root.iter():
        content_desc = elem.get("content-desc", "")
        if "Star rating:" in content_desc:
            return content_desc.split("\n")[0]
    
    return None

def download_app(device_id:str, name:str, nb_apps:int=2, health_check_callback=None):
    """
    Télécharge des apps depuis le Play Store.

    Args:
        device_id: Serial de l'émulateur
        name: Nom de l'app à rechercher
        nb_apps: Nombre d'apps à télécharger
        health_check_callback: Fonction optionnelle qui vérifie la santé de l'émulateur.
                              Doit lever une exception si l'émulateur est offline.

    Returns:
        Liste de tuples (package_name, app_name)

    Raises:
        RuntimeError: Si l'émulateur devient offline pendant le téléchargement
        InterruptedError: Si un arrêt global est demandé
    """
    installed_packages = []

    # Vérification initiale
    if health_check_callback:
        health_check_callback()

    # GERER LE MAIL/MDP par emulateur PS
    PS_verif.manage_PS(device_id, verbose=False)
    
    
    # Checkpoint 1
    if health_check_callback:
        health_check_callback()

    updateUITree(device_id)
    tap = find_node_by_text(device_id, "Search apps")
    adb_utils.tap(device_id, tap["center_x"], tap["center_y"])
    time.sleep(5)
    adb_utils.type_text(device_id, name)
    time.sleep(2)
    adb_utils.press_enter(device_id)
    time.sleep(5)

    # Checkpoint 2
    if health_check_callback:
        health_check_callback()

    nb_general_attemps = 0
    while nb_general_attemps<5 and nb_apps>len(installed_packages):
        # Checkpoint dans la boucle principale
        if health_check_callback:
            health_check_callback()

        updateUITree(device_id)
        nb_general_attemps+=1
        results = get_nodes_with_newline(device_id)
        for res in results:
            if(valid_position(device_id,res["center"][1])):
                #print("App valide.")
                adb_utils.adb_long_tap(res["center"][0], res["center"][1], device_id, 2000)
                time.sleep(2)
                updateUITree(device_id)
                if(not content_desc_contains(device_id, "Add to wishlist")):
                    adb_utils.adb_back(device_id)
                    time.sleep(2)
                elif content_desc_contains(device_id, "Why this ad?"):
                    adb_utils.adb_back(device_id)
                    time.sleep(2)
                elif get_last_node_position(device_id, "Install") == None:
                    adb_utils.adb_back(device_id)
                    time.sleep(2)
                else :
                    #BON PACKAGE
                    #Récupération du nom officiel
                    xml_path = os.path.join("temp", device_id, "Download", "ui.xml")
                    name_app = find_app_name_from_star_rating(xml_path)
                    if name_app:
                        logger.info(f"[{device_id}] Name app: {name_app}")
                    else:
                        logger.warning(f"[{device_id}] Can't find app name")
                        name_app = "UNKNOWN"
                    packages_avant = adb_utils.get_installed_packages(device_id)
                    #print(f"Nombre d'apps avant : {len(packages_avant)}")
                    res_button = get_last_node_position(device_id, "Install")
                    time.sleep(2)
                    adb_utils.tap(device_id, res_button[0], res_button[1])
                    time.sleep(5)
                    #adb_utils.adb_back(emulator_manager.emulator_PS)
                    new_package = None
                    timeout = 300  # 5 minutes
                    start_time = time.time()

                    while time.time() - start_time < timeout:
                        # Checkpoint dans la boucle d'attente d'installation (toutes les 10 secondes)
                        if health_check_callback and (time.time() - start_time) % 10 < 0.5:
                            health_check_callback()

                        packages_apres = adb_utils.get_installed_packages(device_id)
                        time.sleep(0.5)
                        diff = packages_apres - packages_avant
                        if diff:
                            new_package = list(diff)[0] # On ne pop pas tout de suite pour garder la réf
                            
                            # --- VERIFICATION DE LA DISPONIBILITE REELLE ---
                            # On vérifie si pm path renvoie quelque chose
                            check_path = subprocess.run(f"adb -s {device_id} shell pm path {new_package}", 
                                                        shell=True, capture_output=True, text=True)
                            
                            if "package:" in check_path.stdout:
                                # Petite pause de sécurité pour laisser le système relâcher les verrous
                                time.sleep(2)
                                installed_packages.append((new_package, name_app))
                                break

                        time.sleep(2)  # Attente entre deux scans
                    if not new_package:
                        #print("L'installation a pris trop de temps ou a échoué.")
                        if(len(installed_packages)>0):
                            return installed_packages
                        else:
                            raise Exception("Installation too long.")
                    #print("Installed packages : "+str(len(installed_packages)))
                    #print("Needed packages : "+str(nb_apps))
                    if(len(installed_packages)>=nb_apps):
                        #print("Nombre d'applications installés terminé.")
                        return installed_packages
                    continue
            #else:
                #print("App NON valide.")
        #Scroll
        adb_utils.adb_scroll_half_screen(device_id)
        time.sleep(2)
    raise Exception("Too much attempts.")

def check_app_page_status(xml_path: str):
    """
    Analyse un fichier XML pour déterminer si la page Play Store correspond à une app existante ou non trouvée.

    Args:
        xml_path: Chemin vers le fichier ui.xml

    Returns:
        dict avec:
            - "status": "found", "not_found", "age_error" ou "country_error"
            - "install_coords": tuple (x, y) du centre du bouton Install si trouvé, None sinon
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Chercher les indicateurs d'erreur "Item not found"
    for node in root.iter("node"):
        text = node.attrib.get("text", "")
        if text == "Item not found.":
            return {"status": "not_found", "install_coords": None}

    # Chercher les indicateurs d'erreur de pays
    for node in root.iter("node"):
        text = node.attrib.get("text", "")
        if "isn't available in your country" in text or "not available in your country" in text.lower():
            return {"status": "country_error", "install_coords": None}

    # Chercher les indicateurs d'erreur de version
    for node in root.iter("node"):
        text = node.attrib.get("text", "")
        if "Your device isn't compatible with this version" in text:
            return {"status": "version_error", "install_coords": None}

    # Chercher les indicateurs d'erreur d'âge (restriction 18+)
    for node in root.iter("node"):
        text = node.attrib.get("text", "")
        content_desc = node.attrib.get("content-desc", "")

        # Détection via le message de restriction d'âge
        if "restricted access to this app for accounts of anyone under 18" in text:
            return {"status": "age_error", "install_coords": None}

        # Détection via le content-desc PEGI 18
        if "PEGI 18" in content_desc or "Content rating PEGI 18" in content_desc:
            # Vérifier si c'est bien une page de restriction (pas juste un badge PEGI)
            # On cherche aussi le Warning ou le message de restriction
            for inner_node in root.iter("node"):
                inner_text = inner_node.attrib.get("text", "")
                inner_desc = inner_node.attrib.get("content-desc", "")
                if "restricted access" in inner_text.lower() or inner_desc == "Warning":
                    return {"status": "age_error", "install_coords": None}

    # Chercher le bouton Install
    for node in root.iter("node"):
        content_desc = node.attrib.get("content-desc", "")
        text = node.attrib.get("text", "")

        # Le bouton Install peut être identifié par content-desc="Install" ou text="Install"
        if content_desc == "Install" or text == "Install":
            bounds = node.attrib.get("bounds", "")
            if bounds:
                match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
                if match:
                    x1, y1, x2, y2 = map(int, match.groups())
                    center_x = (x1 + x2) // 2
                    center_y = (y1 + y2) // 2
                    return {"status": "found", "install_coords": (center_x, center_y)}

    # Si pas de bouton Install trouvé mais pas d'erreur non plus (cas ambigu)
    return {"status": "not_defined", "install_coords": None}


def check_size_popup(device_id: str):
    """
    Vérifie si une popup de confirmation de taille est affichée et clique sur OK si c'est le cas.

    Args:
        device_id: Serial du device

    Returns:
        bool: True si une popup a été détectée et validée, False sinon
    """
    xml_path = "temp/" + device_id + "/Download/ui.xml"
    updateUITree(device_id)

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Chercher l'indicateur "App size:" qui indique la popup de taille
        has_size_popup = False
        for node in root.iter("node"):
            text = node.attrib.get("text", "")
            if text.startswith("App size:"):
                has_size_popup = True
                break

        if not has_size_popup:
            return False

        logger.info(f"[{device_id}] Popup de taille détectée, validation...")

        # Chercher le bouton OK
        for node in root.iter("node"):
            text = node.attrib.get("text", "")
            node_class = node.attrib.get("class", "")

            if text == "OK" and "Button" in node_class:
                bounds = node.attrib.get("bounds", "")
                if bounds:
                    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
                    if match:
                        x1, y1, x2, y2 = map(int, match.groups())
                        center_x = (x1 + x2) // 2
                        center_y = (y1 + y2) // 2
                        adb_utils.tap(device_id, center_x, center_y)
                        logger.info(f"[{device_id}] Popup de taille validée")
                        return True

        return False
    except Exception as e:
        logger.warning(f"[{device_id}] Erreur check_size_popup: {e}")
        return False


def check_age_verification(device_id: str, package_id: str):
    """
    Vérifie si une popup de vérification d'âge est affichée après avoir cliqué sur Install.

    La popup contient typiquement:
    - Texte "Verify your age to continue"
    - Texte contenant "g.co/play/verifyage"
    - Bouton "Got it"

    Args:
        device_id: Serial du device
        package_id: Package ID de l'app (pour le message d'erreur)

    Raises:
        AgeVerificationError: Si une vérification d'âge est détectée
    """
    xml_path = "temp/" + device_id + "/Download/ui.xml"
    updateUITree(device_id)

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Indicateurs de la popup de vérification d'âge
        has_verify_age_text = False
        has_verifyage_url = False
        has_got_it_button = False

        for node in root.iter("node"):
            text = node.attrib.get("text", "")

            # Détection du titre "Verify your age to continue"
            if "Verify your age" in text:
                has_verify_age_text = True

            # Détection de l'URL de vérification
            if "g.co/play/verifyage" in text or "verifyage" in text.lower():
                has_verifyage_url = True

            # Détection du bouton "Got it"
            if text == "Got it":
                node_class = node.attrib.get("class", "")
                if "Button" in node_class:
                    has_got_it_button = True

        # Si on détecte au moins 2 indicateurs, c'est bien la popup de vérification d'âge
        indicators_count = sum([has_verify_age_text, has_verifyage_url, has_got_it_button])

        if indicators_count >= 2:
            logger.info(f"[{device_id}] Vérification d'âge détectée pour {package_id}")
            # Cliquer sur "Got it" pour fermer la popup
            for node in root.iter("node"):
                text = node.attrib.get("text", "")
                if text == "Got it":
                    bounds = node.attrib.get("bounds", "")
                    if bounds:
                        match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
                        if match:
                            x1, y1, x2, y2 = map(int, match.groups())
                            center_x = (x1 + x2) // 2
                            center_y = (y1 + y2) // 2
                            adb_utils.tap(device_id, center_x, center_y)
                            time.sleep(1)
                            break

            # Retour arrière pour quitter la page
            adb_utils.adb_back(device_id)
            raise AgeVerificationError(package_id)

    except AgeVerificationError:
        raise  # Re-lever l'exception
    except Exception as e:
        logger.warning(f"[{device_id}] Erreur check_age_verification: {e}")


def open_play_store_page(device_id: str, app_id: str):
    """
    Ouvre la page Play Store d'une application spécifique sur un device.

    Args:
        device_id: Serial du device (ex: "emulator-5554")
        app_id: Package ID de l'application (ex: "com.whatsapp")
    """
    cmd = [
        "adb", "-s", device_id,
        "shell", "am", "start",
        "-a", "android.intent.action.VIEW",
        "-d", f"market://details?id={app_id}"
    ]
    subprocess.run(cmd, check=False)

def download_from_package(device_id:str, package_id:str, health_check_callback=None):
    """
    Télécharge une application depuis le Play Store via son package ID.

    Args:
        device_id: Serial du device (ex: "emulator-5554")
        package_id: Package ID de l'application (ex: "com.whatsapp")
        health_check_callback: Fonction optionnelle de vérification de santé

    Returns:
        bool: True si l'installation a réussi, False sinon
    """
    if health_check_callback:
        health_check_callback()

    #PS_verif.verif_status_PS(device_id, False)
    #PS_verif.manage_PS()
    # Ouvrir la page Play Store de l'app
    open_play_store_page(device_id, package_id)

    # Attendre que la page soit chargée (bouton Install visible)
    xml_path = "temp/" + device_id + "/Download/ui.xml"
    result = None
    max_attempts = 3  # 3 tentatives max (~30 secondes)

    for attempt in range(max_attempts):
        time.sleep(10)

        if health_check_callback:
            health_check_callback()

        updateUITree(device_id)
        result = check_app_page_status(xml_path)

        if(result["status"] !="not_defined"):
            break

        logger.info(f"[{device_id}] Attente chargement page... ({attempt + 1}/{max_attempts})")

    logger.info(f"[{device_id}] Page chargée après {attempt + 1} tentatives")

    if result["status"] == "not_found":
        logger.warning(f"[{device_id}] App {package_id} non trouvée sur le Play Store")
        adb_utils.adb_back(device_id)
        return False

    if result["status"] == "age_error":
        logger.warning(f"[{device_id}] App {package_id} bloquée : restriction d'âge (18+)")
        adb_utils.adb_back(device_id)
        return False

    if result["status"] == "country_error":
        logger.warning(f"[{device_id}] App {package_id} non disponible dans ce pays")
        adb_utils.adb_back(device_id)
        raise CountryNotAvailableError(package_id)

    if result["status"] == "version_error":
        logger.warning(f"[{device_id}] App {package_id} non compatible avec cette version du device")
        adb_utils.adb_back(device_id)
        raise VersionNotCompatibleError(package_id)

    # App trouvée, cliquer sur Install
    install_coords = result["install_coords"]
    if install_coords is None:
        logger.warning(f"[{device_id}] Bouton Install non trouvé pour {package_id}")
        adb_utils.adb_back(device_id)
        return False

    # Récupérer les packages avant installation
    packages_avant = adb_utils.get_installed_packages(device_id)

    # Cliquer sur le bouton Install
    adb_utils.tap(device_id, install_coords[0], install_coords[1])
    time.sleep(3)

    # Vérifier si une popup de taille apparaît et la valider
    check_size_popup(device_id)

    time.sleep(2)

    if health_check_callback:
        health_check_callback()

    # Vérifier si une popup de vérification d'âge apparaît (quelques secondes après le clic Install)
    time.sleep(3)
    check_age_verification(device_id, package_id)

    # Attendre l'installation
    timeout = 180  # 3 minutes
    start_time = time.time()

    while time.time() - start_time < timeout:
        if health_check_callback and (time.time() - start_time) % 10 < 0.5:
            health_check_callback()

        packages_apres = adb_utils.get_installed_packages(device_id)
        diff = packages_apres - packages_avant

        if diff:
            new_package = list(diff)[0]
            # Vérifier que le package est bien disponible
            check_path = subprocess.run(
                f"adb -s {device_id} shell pm path {new_package}",
                shell=True, capture_output=True, text=True
            )
            if "package:" in check_path.stdout:
                time.sleep(2)
                logger.info(f"[{device_id}] App {package_id} installée avec succès")
                adb_utils.adb_back(device_id)
                return True

        time.sleep(2)

    logger.warning(f"[{device_id}] Timeout: installation de {package_id} trop longue")
    adb_utils.adb_back(device_id)
    return False

#updateUITree()

if __name__ == "__main__":
    result = download_from_package("emulator-5554", "br.com.isaralimentos")
    print(result)