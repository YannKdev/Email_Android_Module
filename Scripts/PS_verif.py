# PS_verif.py
import xml.etree.ElementTree as ET
import os
import time
import logging
from typing import Dict, List, Optional
import Database
import download_2
import adb_utils

logger = logging.getLogger(__name__)

# ==========================================================
# =================== ÉTATS DÉFINITIONS ====================
# ==========================================================

# Définition des éléments clés pour chaque état
STATE_SIGNATURES = {
    "PLAY_STORE_SIGNIN": {
        "package": "com.android.vending",
        "key_elements": [
            {"text": "Sign in", "class": "android.widget.Button"},
            {"text": "Sign in to find the latest Android apps", "class": "android.widget.TextView"}
        ]
    },

    "SIGN_IN_EMAIL_FIELD": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"class": "android.webkit.WebView"},
            {"text": "NEXT", "class": "android.widget.Button"},
            {"resource-id": "com.google.android.gms:id/sud_layout_content"}
        ]
    },

    "PASSWORD_FIELD": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Show password", "class": "android.widget.CheckBox"},
            {"text": "FORGOT PASSWORD?", "class": "android.widget.Button"},
            {"text": "NEXT", "class": "android.widget.Button"},
            {"password": "true", "class": "android.widget.EditText"}
        ]
    },

    "LEVEL_UP_EXPERIENCE": {
        "package": "com.android.vending",
        "key_elements": [
            {"text": "Level up your experience", "class": "android.widget.TextView"},
            {"text": "Not now", "class": "android.widget.Button"},
            {"text": "I'm in!", "class": "android.widget.Button"}
        ]
    },

    "ACCOUNT_ALREADY_EXISTS": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "This account already exists on your device"},
            {"text": "NEXT", "class": "android.widget.Button"}
        ]
    },

    "CHECKING_INFO": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Checking info"},
            {"class": "android.widget.ProgressBar"}
        ]
    },

    "NORMAL_UI": {
        "package": "com.android.vending",
        "key_elements": [
            {"text": "For you", "class": "android.widget.TextView"},
            {"text": "Games", "class": "android.widget.TextView"},
            {"text": "Apps", "class": "android.widget.TextView"},
            {"text": "Search", "class": "android.widget.TextView"},
            {"content-desc": "Signed in as"}
        ]
    },

    "WELCOME_GOOGLE_PLAY": {
        "package": "com.android.vending",
        "key_elements": [
            {"text": "Welcome to Google Play", "class": "android.widget.TextView"},
            {"text": "Get started", "class": "android.widget.Button"},
            {"text": "Not now", "class": "android.widget.Button"}
        ]
    },

    "LOCAL_RECOMMENDATIONS": {
        "package": "com.android.vending",
        "key_elements": [
            {"text": "Want to see local recommendations in Google Play?"},
            {"text": "Continue", "class": "android.widget.Button"},
            {"text": "No thanks", "class": "android.widget.Button"}
        ]
    },

    "SKIP_NEVER_LOOSE_CONTACT": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Never lose your contacts"},
            {"text": "Don't turn on", "class": "android.widget.Button"},
            {"text": "Turn on Backup", "class": "android.widget.Button"}
        ]
    },

    "MAKE_SURE_ALWAYS_LOGIN": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Make sure you can always get into your account"}
        ]
    },

    "GOOGLE_BACK_UP": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Google services", "class": "android.widget.TextView"},
            {"text": "Backup", "class": "android.widget.TextView"},
            {"text": "Back up device data", "class": "android.widget.TextView"},
            {"text": "MORE", "class": "android.widget.Button"}
        ]
    },

    "ADDITIONAL_SEARCH_SERVICE": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Improve your experience"}
        ]
    },

    "GOOGLE_OPTIMIZED": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "Get helpful suggestions in Google apps"}
        ]
    },

    "AGREE_TERMS": {
        "package": "com.google.android.gms",
        "key_elements": [
            {"text": "I agree", "class": "android.widget.Button"},
            {"text": "Google Terms of Service", "class": "android.widget.Button"},
            {"text": "Privacy Policy", "class": "android.widget.Button"}
        ]
    }
}


# ==========================================================
# =================== FONCTIONS UTILITAIRES ================
# ==========================================================

def get_ui_dump(device_serial: str, output_path: str = "temp/ui.xml") -> bool:
    """
    Récupère le dump UI de l'émulateur en utilisant adb_utils.take_ui_xml().

    Args:
        device_serial: Serial de l'émulateur (ex: "emulator-5554")
        output_path: Chemin où sauvegarder le fichier XML

    Returns:
        True si réussi, False sinon
    """
    try:
        # Créer le dossier si nécessaire
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Utiliser adb_utils.take_ui_xml() pour récupérer le dump
        adb_utils.take_ui_xml(device_serial, output_path)

        return os.path.exists(output_path)
    except Exception as e:
        logger.error(f"Erreur lors du dump UI: {e}")
        return False


def parse_xml(xml_path: str) -> Optional[ET.Element]:
    """
    Parse le fichier XML.

    Args:
        xml_path: Chemin vers le fichier XML

    Returns:
        Root element du XML ou None si erreur
    """
    try:
        tree = ET.parse(xml_path)
        return tree.getroot()
    except Exception as e:
        logger.error(f"Erreur lors du parsing XML: {e}")
        return None


def find_element_recursive(
    root: ET.Element,
    attributes: Dict[str, str],
    max_depth: int = 50,
    _depth: int = 0,
) -> bool:
    """
    Recherche récursivement un élément avec les attributs donnés.

    Args:
        root: Élément racine
        attributes: Dictionnaire des attributs à rechercher
        max_depth: Profondeur maximale de récursion (Fix 7)
        _depth: Profondeur actuelle (usage interne)

    Returns:
        True si trouvé, False sinon
    """
    if _depth >= max_depth:
        return False

    # Vérifier si l'élément actuel correspond
    match = True
    for key, value in attributes.items():
        elem_value = root.get(key, "")

        # Gestion spéciale pour les recherches partielles
        if key == "text" or key == "content-desc":
            if value not in elem_value:
                match = False
                break
        else:
            if elem_value != value:
                match = False
                break

    if match:
        return True

    # Rechercher dans les enfants
    for child in root:
        if find_element_recursive(child, attributes, max_depth, _depth + 1):
            return True

    return False


def count_matching_elements(root: ET.Element, key_elements: List[Dict[str, str]], debug: bool = False) -> int:
    """
    Compte combien d'éléments clés sont trouvés dans le XML.

    Args:
        root: Élément racine du XML
        key_elements: Liste des éléments clés à rechercher
        debug: Afficher les détails de la recherche

    Returns:
        Nombre d'éléments trouvés
    """
    count = 0
    for key_elem in key_elements:
        # Créer un dictionnaire d'attributs à rechercher
        search_attrs = {}
        for k, v in key_elem.items():
            if k == "type":
                search_attrs["class"] = f"android.widget.{v}"
            else:
                search_attrs[k] = v

        found = find_element_recursive(root, search_attrs)
        if found:
            count += 1
            if debug:
                logger.debug(f"      ✓ Trouvé: {search_attrs}")
        else:
            if debug:
                logger.debug(f"      ✗ Non trouvé: {search_attrs}")

    return count




# ==========================================================
# =================== FONCTION PRINCIPALE ==================
# ==========================================================

def verif_status_PS(device_serial: str, verbose: bool = True) -> Optional[str]:
    """
    Analyse l'état actuel d'un émulateur Play Store.
    Utilise le dump UI déjà récupéré (ne récupère PAS un nouveau dump).

    Args:
        device_serial: Serial de l'émulateur (ex: "emulator-5554")
        verbose: Afficher les messages de debug

    Returns:
        État détecté (str) ou None si erreur
    """

    # 1. Utiliser le dump UI existant (déjà récupéré par manage_PS via download_2.updateUITree)
    ui_path = f"temp/{device_serial}/Download/ui.xml"
    if verbose:
        logger.info(f"📱 [{device_serial}] Analyse de l'UI...")

    # Vérifier que le fichier existe
    if not os.path.exists(ui_path):
        if verbose:
            logger.error(f"❌ [{device_serial}] Le fichier UI n'existe pas: {ui_path}")
        return None

    # 2. Parser le XML
    root = parse_xml(ui_path)
    if root is None:
        if verbose:
            logger.error(f"❌ [{device_serial}] Impossible de parser le XML")
        return None

    # 3. Analyser l'état en comparant avec les signatures
    best_match = None
    best_score = 0
    best_element_count = 0  # Pour départager les ex-aequo à 100%

    for state_name, signature in STATE_SIGNATURES.items():
        # Compter les éléments trouvés
        key_elements = signature["key_elements"]
        found_count = count_matching_elements(root, key_elements, debug=verbose)
        total_count = len(key_elements)

        # Calculer le score (pourcentage de correspondance)
        score = (found_count / total_count) * 100 if total_count > 0 else 0

        # Afficher uniquement les scores > 0%
        if verbose and score > 0:
            logger.debug(f"   {state_name}: {found_count}/{total_count} éléments trouvés ({score:.1f}%)")

        # Garder le meilleur match
        # En cas d'égalité à 100%, préférer l'état avec le plus d'éléments (plus spécifique)
        if score > best_score or (score == best_score and score == 100 and total_count > best_element_count):
            best_score = score
            best_match = state_name
            best_element_count = total_count

    # 4. Retourner le résultat
    if best_score >= 50:  # Seuil de confiance de 50%
        if verbose:
            logger.info(f"✅ [{device_serial}] État détecté: {best_match} ({best_score:.1f}%)")
        return best_match
    else:
        if verbose:
            logger.warning(f"⚠️ [{device_serial}] État inconnu (meilleur score: {best_score:.1f}%)")
        return "UNKNOWN"


# ==========================================================
# =================== ACTIONS PAR ÉTAT =====================
# ==========================================================

def tap_element(device_serial: str, text: str = None, resource_id: str = None, class_name: str = None) -> bool:
    """
    Clique sur un élément de l'UI en utilisant adb_utils.tap().
    Utilise le dump UI déjà existant (ne récupère PAS un nouveau dump).

    Args:
        device_serial: Serial de l'émulateur
        text: Texte de l'élément à cliquer
        resource_id: Resource-id de l'élément
        class_name: Classe de l'élément (optionnel pour affiner la recherche)

    Returns:
        True si succès, False sinon
    """
    try:
        import re

        # Construire le dictionnaire de recherche
        search_attrs = {}
        if text:
            search_attrs["text"] = text
        if resource_id:
            search_attrs["resource-id"] = resource_id
        if class_name:
            search_attrs["class"] = class_name

        if not search_attrs:
            logger.error(f"❌ [{device_serial}] Aucun attribut de recherche fourni")
            return False

        # Utiliser le dump UI EXISTANT (déjà récupéré par download_2.updateUITree)
        ui_path = f"temp/{device_serial}/Download/ui.xml"

        # Vérifier que le fichier existe
        if not os.path.exists(ui_path):
            logger.error(f"❌ [{device_serial}] Le fichier UI n'existe pas: {ui_path}")
            return False

        root = parse_xml(ui_path)
        if root is None:
            logger.error(f"❌ [{device_serial}] Impossible de parser le XML")
            return False

        # Trouver l'élément
        elem = find_element_with_bounds(root, search_attrs, debug=False)
        if elem is not None and elem.get("bounds"):
            bounds = elem.get("bounds")
            # Format: [x1,y1][x2,y2]
            match = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
            if match:
                x1, y1, x2, y2 = map(int, match.groups())
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2

                # Utiliser adb_utils.tap() au lieu de subprocess
                adb_utils.tap(device_serial, center_x, center_y)
                return True
        else:
            logger.error(f"❌ [{device_serial}] Élément non trouvé: {search_attrs}")

        return False
    except Exception as e:
        logger.error(f"❌ Erreur lors du tap: {e}")
        import traceback
        traceback.print_exc()
        return False


def find_element_with_bounds(
    root: ET.Element,
    attributes: Dict[str, str],
    debug: bool = False,
    max_depth: int = 50,
    _depth: int = 0,
) -> Optional[ET.Element]:
    """
    Recherche récursivement un élément avec les attributs donnés et retourne l'élément.
    TOUS les attributs doivent correspondre pour qu'un élément soit retourné.

    Args:
        root: Élément racine
        attributes: Dictionnaire des attributs à rechercher
        debug: Afficher les logs de debug
        max_depth: Profondeur maximale de récursion (Fix 7)
        _depth: Profondeur actuelle (usage interne)

    Returns:
        Element trouvé ou None
    """
    if _depth >= max_depth:
        return None

    # Vérifier si l'élément actuel correspond à TOUS les attributs
    match = True
    for key, value in attributes.items():
        elem_value = root.get(key, "")

        # Pour le texte, recherche partielle (contient)
        if key == "text" or key == "content-desc":
            if value not in elem_value:
                match = False
                if debug and elem_value:
                    logger.debug(f"  Text mismatch: '{value}' not in '{elem_value}'")
                break
        # Pour les autres attributs (class, resource-id, etc.), correspondance exacte
        else:
            if elem_value != value:
                match = False
                if debug and elem_value:
                    logger.debug(f"  {key} mismatch: '{value}' != '{elem_value}'")
                break

    if match and all(root.get(k) for k in attributes.keys()):
        if debug:
            logger.debug(f"  Match found: {dict(root.attrib)}")
        return root

    # Rechercher dans les enfants
    for child in root:
        result = find_element_with_bounds(child, attributes, debug, max_depth, _depth + 1)
        if result is not None:
            return result

    return None


def input_text(device_serial: str, text: str) -> bool:
    """
    Saisit du texte dans l'élément actuellement focus en utilisant adb_utils.type_text().

    Args:
        device_serial: Serial de l'émulateur
        text: Texte à saisir

    Returns:
        True si succès, False sinon
    """
    try:
        adb_utils.type_text(device_serial, text)
        return True
    except Exception as e:
        logger.error(f"❌ Erreur lors de la saisie: {e}")
        return False


def press_back(device_serial: str) -> bool:
    """
    Appuie sur le bouton retour en utilisant adb_utils.adb_back().

    Args:
        device_serial: Serial de l'émulateur

    Returns:
        True si succès, False sinon
    """
    try:
        adb_utils.adb_back(device_serial)
        return True
    except Exception:
        return False


def handle_state_action(device_serial: str, state: str, email: str = None, password: str = None) -> bool:
    """
    Exécute l'action appropriée en fonction de l'état détecté.

    Args:
        device_serial: Serial de l'émulateur
        state: État actuel du Play Store
        email: Email à utiliser (si nécessaire)
        password: Mot de passe à utiliser (si nécessaire)

    Returns:
        True si action exécutée, False sinon
    """
    logger.info(f"🔧 [{device_serial}] Action pour état: {state}")

    if state == "PLAY_STORE_SIGNIN":
        # Cliquer sur le bouton "Sign in" (spécifier la classe pour éviter de cliquer sur le TextView)
        result = tap_element(device_serial, text="Sign in", class_name="android.widget.Button")
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "SIGN_IN_EMAIL_FIELD":
        # Saisir l'email et valider avec Enter
        if email:
            # Envoyer TAB pour naviguer jusqu'au champ email
            adb_utils.send_tab(device_serial)
            time.sleep(2)

            # Envoyer TAB à nouveau pour s'assurer d'être sur le bon champ
            adb_utils.send_tab(device_serial)

            time.sleep(2)
            adb_utils.send_tab(device_serial)
            time.sleep(2)
            
            # Taper l'email directement
            if input_text(device_serial, email):
                time.sleep(1)
                # Valider avec la touche Enter
                try:
                    adb_utils.press_enter(device_serial)
                    time.sleep(5)  # Attendre le rafraîchissement
                    return True
                except Exception as e:
                    logger.error(f"❌ Erreur lors de la validation: {e}")
                    return False
        return False

    elif state == "PASSWORD_FIELD":
        # Saisir le mot de passe directement (le clavier s'affiche automatiquement)
        if password:
            # Saisir le mot de passe directement
            if input_text(device_serial, password):
                time.sleep(1)
                # Simuler la touche Entrée pour valider
                try:
                    adb_utils.press_enter(device_serial)
                    time.sleep(5)  # Attendre le rafraîchissement
                    return True
                except Exception as e:
                    logger.error(f"❌ Erreur lors de la validation: {e}")
                    return False
        return False

    elif state == "LEVEL_UP_EXPERIENCE":
        # Cliquer sur "Not now"
        result = tap_element(device_serial, text="Not now")
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "ACCOUNT_ALREADY_EXISTS":
        # Cliquer sur retour ou NEXT pour continuer
        result = tap_element(device_serial, text="NEXT") or press_back(device_serial)
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "CHECKING_INFO":
        # Attendre que la vérification se termine
        logger.info(f"⏳ [{device_serial}] Attente de la vérification...")
        time.sleep(5)
        return True

    elif state == "WELCOME_GOOGLE_PLAY":
        # Cliquer sur "Get started" ou "Not now"
        result = tap_element(device_serial, text="Not now") or tap_element(device_serial, text="Get started")
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "LOCAL_RECOMMENDATIONS":
        # Envoyer 3 TAB puis Enter pour valider "No thanks"
        adb_utils.send_tab(device_serial)
        time.sleep(1)
        adb_utils.send_tab(device_serial)
        time.sleep(1)
        adb_utils.send_tab(device_serial)
        time.sleep(1)
        try:
            adb_utils.press_enter(device_serial)
            time.sleep(5)  # Attendre le rafraîchissement
            return True
        except Exception as e:
            logger.error(f"❌ Erreur lors de la validation: {e}")
            return False

    elif state == "SKIP_NEVER_LOOSE_CONTACT":
        # Cliquer sur skip ou no thanks
        result = tap_element(device_serial, text="Skip") or tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "MAKE_SURE_ALWAYS_LOGIN":
        # Cliquer sur skip ou no thanks
        result = tap_element(device_serial, text="Skip") or tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "GOOGLE_BACK_UP":
        # Réinitialiser et rouvrir le Play Store
        download_2.reset_open_play_store(device_serial)
        time.sleep(5)
        return True

    elif state == "ADDITIONAL_SEARCH_SERVICE":
        # Cliquer sur skip ou no
        result = tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "GOOGLE_OPTIMIZED":
        # Cliquer sur no thanks
        result = tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    elif state == "AGREE_TERMS":
        # Cliquer sur "I agree"
        result = tap_element(device_serial, text="I agree", class_name="android.widget.Button")
        if result:
            time.sleep(5)  # Attendre le rafraîchissement
        return result

    return False


# ==========================================================
# =================== PIPELINE PRINCIPAL ===================
# ==========================================================

def manage_PS(device_serial: str, email: str = None, password: str = None,
              max_attempts: int = 15, verbose: bool = True) -> bool:
    """
    Gère le pipeline de configuration du Play Store jusqu'à obtenir l'état NORMAL_UI.

    Args:
        device_serial: Serial de l'émulateur (ex: "emulator-5554")
        email: Email du compte Google (optionnel)
        password: Mot de passe du compte Google (optionnel)
        max_attempts: Nombre maximum de tentatives (défaut: 15)
        verbose: Afficher les logs détaillés

    Returns:
        True si succès (NORMAL_UI atteint), False sinon
    """
    logger.info(f"🚀 [{device_serial}] Démarrage du pipeline Play Store")

    attempt = 0
    current_state = None
    none_state_count = 0  # Compteur pour les états None consécutifs

    download_2.reset_open_play_store(device_serial)
    time.sleep(15)
    while attempt < max_attempts:
        attempt += 1
        logger.info(f"🔄 [{device_serial}] Vérification Play Store : {attempt}/{max_attempts}")

        download_2.updateUITree(device_serial)
        time.sleep(5)
        # 1. Vérifier l'état actuel
        current_state = verif_status_PS(device_serial, verbose=verbose)

        if current_state is None:
            none_state_count += 1
            logger.error(f"❌ [{device_serial}] Impossible de récupérer l'état (tentative {none_state_count}/2)")

            if none_state_count >= 2:
                logger.error(f"❌ [{device_serial}] Échec: impossible de récupérer l'état 2 fois consécutives")
                Database.update_emulator_status(device_serial, "PLAY_STORE_ERROR_UNKNOWN_STATUS")
                return False

            time.sleep(15)
            continue
        else:
            # Réinitialiser le compteur si on récupère un état valide
            none_state_count = 0

        # 2. Mettre à jour le status dans la DB
        Database.update_emulator_status(device_serial, current_state)

        # 3. Vérifier si on a atteint l'objectif
        if current_state == "NORMAL_UI":
            logger.info(f"✅ [{device_serial}] Play Store configuré avec succès!")
            Database.update_emulator_status(device_serial, "PLAY_STORE_OK")
            return True

        # 4. Vérifier si état inconnu - PAUSE POUR INSPECTION HUMAINE
        if current_state == "UNKNOWN":
            logger.error(f"❌ [{device_serial}] Échec: état inconnu - PAUSE POUR INSPECTION HUMAINE")
            Database.update_emulator_status(device_serial, "PAUSED_UNKNOWN_STATE")
            return False

        # 5. Exécuter l'action appropriée
        action_success = handle_state_action(device_serial, current_state, email, password)

        if action_success:
            logger.info(f"✅ [{device_serial}] Action exécutée avec succès")
        else:
            logger.error(f"❌ [{device_serial}] Échec: action échouée pour l'état {current_state} - PAUSE POUR INSPECTION")
            Database.update_emulator_status(device_serial, "PAUSED_ACTION_FAILED")
            return False

        # 6. Attendre avant la prochaine vérification (le sleep est déjà dans handle_state_action)
        time.sleep(2)

    # Si on arrive ici, on a dépassé max_attempts - PAUSE POUR INSPECTION
    logger.error(f"❌ [{device_serial}] Échec: limite de tentatives atteinte ({max_attempts}) - Dernier état: {current_state}")
    logger.warning(f"⏸️ [{device_serial}] PAUSE POUR INSPECTION HUMAINE")
    Database.update_emulator_status(device_serial, "PAUSED_MAX_ATTEMPTS")
    return False


# ==========================================================
# =================== EXEMPLE D'UTILISATION ================
# ==========================================================

if __name__ == "__main__":
    import accounts_manager
    # Exemple d'utilisation
    serial = "emulator-5554"
    #Database.truncate_all_tables()
    #Database.add_emulator(serial, "PS")
    #Database.add_account("u2348857978@gmail.com", "raderbay.frere")
    #Database.link_account_to_emulator("u2348857978@gmail.com", serial)
    time.sleep(10)
    # 1. S'assurer qu'un compte est lié (attribution si nécessaire)
    account = accounts_manager.get_emulator_account(serial)
    if not account:
        logger.info(f"🔑 [{serial}] Aucun compte lié, attribution en cours...")
        account = accounts_manager.assign_account_to_emulator(serial)

        if not account:
            logger.error(f"❌ [{serial}] Aucun compte disponible, attente...")
            
    # 2. Vérifier l'état du Play Store avant de prendre une app
    download_2.updateUITree(serial)
    time.sleep(2)
    ps_state = verif_status_PS(serial, verbose=False)

    # 3. Si le Play Store n'est pas en NORMAL_UI, gérer avec manage_PS
    if not ps_state or ps_state != "NORMAL_UI":
        logger.info(f"🔧 [{serial}] Play Store état: {ps_state}, gestion avec manage_PS...")

        # TOUJOURS passer le compte à manage_PS
        success = manage_PS(
            device_serial=serial,
            email=account['email'],
            password=account['mdp'],
            max_attempts=15,
            verbose=False
        )

        if not success:
            logger.error(f"❌ [{serial}] Échec gestion Play Store, attente...")
            Database.update_emulator_status(serial, "ERROR_MANAGING_PLAY_STORE")
            time.sleep(10)

        
