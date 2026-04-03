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
# =================== STATE DEFINITIONS ====================
# ==========================================================

# Definition of key elements for each state
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
    Retrieves the UI dump of the emulator using adb_utils.take_ui_xml().

    Args:
        device_serial: Emulator serial (e.g. "emulator-5554")
        output_path: Path where the XML file should be saved

    Returns:
        True if successful, False otherwise
    """
    try:
        # Create the directory if needed
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Use adb_utils.take_ui_xml() to retrieve the dump
        adb_utils.take_ui_xml(device_serial, output_path)

        return os.path.exists(output_path)
    except Exception as e:
        logger.error(f"Error during UI dump: {e}")
        return False


def parse_xml(xml_path: str) -> Optional[ET.Element]:
    """
    Parses the XML file.

    Args:
        xml_path: Path to the XML file

    Returns:
        Root element of the XML or None if error
    """
    try:
        tree = ET.parse(xml_path)
        return tree.getroot()
    except Exception as e:
        logger.error(f"Error during XML parsing: {e}")
        return None


def find_element_recursive(
    root: ET.Element,
    attributes: Dict[str, str],
    max_depth: int = 50,
    _depth: int = 0,
) -> bool:
    """
    Recursively searches for an element with the given attributes.

    Args:
        root: Root element
        attributes: Dictionary of attributes to search for
        max_depth: Maximum recursion depth (Fix 7)
        _depth: Current depth (internal use)

    Returns:
        True if found, False otherwise
    """
    if _depth >= max_depth:
        return False

    # Check whether the current element matches
    match = True
    for key, value in attributes.items():
        elem_value = root.get(key, "")

        # Special handling for partial searches
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

    # Search in children
    for child in root:
        if find_element_recursive(child, attributes, max_depth, _depth + 1):
            return True

    return False


def count_matching_elements(root: ET.Element, key_elements: List[Dict[str, str]], debug: bool = False) -> int:
    """
    Counts how many key elements are found in the XML.

    Args:
        root: Root element of the XML
        key_elements: List of key elements to search for
        debug: Display search details

    Returns:
        Number of elements found
    """
    count = 0
    for key_elem in key_elements:
        # Build a dictionary of attributes to search for
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
                logger.debug(f"      ✓ Found: {search_attrs}")
        else:
            if debug:
                logger.debug(f"      ✗ Not found: {search_attrs}")

    return count




# ==========================================================
# =================== FONCTION PRINCIPALE ==================
# ==========================================================

def verif_status_PS(device_serial: str, verbose: bool = True) -> Optional[str]:
    """
    Analyses the current state of a Play Store emulator.
    Uses the already-retrieved UI dump (does NOT fetch a new dump).

    Args:
        device_serial: Emulator serial (e.g. "emulator-5554")
        verbose: Display debug messages

    Returns:
        Detected state (str) or None if error
    """

    # 1. Use the existing UI dump (already fetched by manage_PS via download_2.updateUITree)
    ui_path = f"temp/{device_serial}/Download/ui.xml"
    if verbose:
        logger.info(f"📱 [{device_serial}] Analyse de l'UI...")

    # Check that the file exists
    if not os.path.exists(ui_path):
        if verbose:
            logger.error(f"❌ [{device_serial}] UI file does not exist: {ui_path}")
        return None

    # 2. Parse the XML
    root = parse_xml(ui_path)
    if root is None:
        if verbose:
            logger.error(f"❌ [{device_serial}] Unable to parse the XML")
        return None

    # 3. Analyse the state by comparing against signatures
    best_match = None
    best_score = 0
    best_element_count = 0  # To break ties at 100%

    for state_name, signature in STATE_SIGNATURES.items():
        # Count found elements
        key_elements = signature["key_elements"]
        found_count = count_matching_elements(root, key_elements, debug=verbose)
        total_count = len(key_elements)

        # Calculate the score (match percentage)
        score = (found_count / total_count) * 100 if total_count > 0 else 0

        # Display only scores > 0%
        if verbose and score > 0:
            logger.debug(f"   {state_name}: {found_count}/{total_count} elements found ({score:.1f}%)")

        # Keep the best match
        # In case of a tie at 100%, prefer the state with the most elements (more specific)
        if score > best_score or (score == best_score and score == 100 and total_count > best_element_count):
            best_score = score
            best_match = state_name
            best_element_count = total_count

    # 4. Return the result
    if best_score >= 50:  # Confidence threshold of 50%
        if verbose:
            logger.info(f"✅ [{device_serial}] Detected state: {best_match} ({best_score:.1f}%)")
        return best_match
    else:
        if verbose:
            logger.warning(f"⚠️ [{device_serial}] Unknown state (best score: {best_score:.1f}%)")
        return "UNKNOWN"


# ==========================================================
# =================== ACTIONS PER STATE ====================
# ==========================================================

def tap_element(device_serial: str, text: str = None, resource_id: str = None, class_name: str = None) -> bool:
    """
    Clicks on a UI element using adb_utils.tap().
    Uses the already-existing UI dump (does NOT fetch a new dump).

    Args:
        device_serial: Emulator serial
        text: Text of the element to click
        resource_id: Resource-id of the element
        class_name: Class of the element (optional, to narrow down the search)

    Returns:
        True if successful, False otherwise
    """
    try:
        import re

        # Build the search dictionary
        search_attrs = {}
        if text:
            search_attrs["text"] = text
        if resource_id:
            search_attrs["resource-id"] = resource_id
        if class_name:
            search_attrs["class"] = class_name

        if not search_attrs:
            logger.error(f"❌ [{device_serial}] No search attributes provided")
            return False

        # Use the EXISTING UI dump (already fetched by download_2.updateUITree)
        ui_path = f"temp/{device_serial}/Download/ui.xml"

        # Check that the file exists
        if not os.path.exists(ui_path):
            logger.error(f"❌ [{device_serial}] UI file does not exist: {ui_path}")
            return False

        root = parse_xml(ui_path)
        if root is None:
            logger.error(f"❌ [{device_serial}] Unable to parse the XML")
            return False

        # Find the element
        elem = find_element_with_bounds(root, search_attrs, debug=False)
        if elem is not None and elem.get("bounds"):
            bounds = elem.get("bounds")
            # Format: [x1,y1][x2,y2]
            match = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
            if match:
                x1, y1, x2, y2 = map(int, match.groups())
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2

                # Use adb_utils.tap() instead of subprocess
                adb_utils.tap(device_serial, center_x, center_y)
                return True
        else:
            logger.error(f"❌ [{device_serial}] Element not found: {search_attrs}")

        return False
    except Exception as e:
        logger.error(f"❌ Error during tap: {e}")
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
    Recursively searches for an element with the given attributes and returns it.
    ALL attributes must match for an element to be returned.

    Args:
        root: Root element
        attributes: Dictionary of attributes to search for
        debug: Display debug logs
        max_depth: Maximum recursion depth (Fix 7)
        _depth: Current depth (internal use)

    Returns:
        Found element or None
    """
    if _depth >= max_depth:
        return None

    # Check whether the current element matches ALL attributes
    match = True
    for key, value in attributes.items():
        elem_value = root.get(key, "")

        # For text, partial search (contains)
        if key == "text" or key == "content-desc":
            if value not in elem_value:
                match = False
                if debug and elem_value:
                    logger.debug(f"  Text mismatch: '{value}' not in '{elem_value}'")
                break
        # For other attributes (class, resource-id, etc.), exact match
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

    # Search in children
    for child in root:
        result = find_element_with_bounds(child, attributes, debug, max_depth, _depth + 1)
        if result is not None:
            return result

    return None


def input_text(device_serial: str, text: str) -> bool:
    """
    Types text into the currently focused element using adb_utils.type_text().

    Args:
        device_serial: Emulator serial
        text: Text to type

    Returns:
        True if successful, False otherwise
    """
    try:
        adb_utils.type_text(device_serial, text)
        return True
    except Exception as e:
        logger.error(f"❌ Error during text input: {e}")
        return False


def press_back(device_serial: str) -> bool:
    """
    Presses the back button using adb_utils.adb_back().

    Args:
        device_serial: Emulator serial

    Returns:
        True if successful, False otherwise
    """
    try:
        adb_utils.adb_back(device_serial)
        return True
    except Exception:
        return False


def handle_state_action(device_serial: str, state: str, email: str = None, password: str = None) -> bool:
    """
    Executes the appropriate action based on the detected state.

    Args:
        device_serial: Emulator serial
        state: Current Play Store state
        email: Email to use (if needed)
        password: Password to use (if needed)

    Returns:
        True if action was executed, False otherwise
    """
    logger.info(f"🔧 [{device_serial}] Action for state: {state}")

    if state == "PLAY_STORE_SIGNIN":
        # Click the "Sign in" button (specify class to avoid clicking the TextView)
        result = tap_element(device_serial, text="Sign in", class_name="android.widget.Button")
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "SIGN_IN_EMAIL_FIELD":
        # Enter the email and confirm with Enter
        if email:
            # Send TAB to navigate to the email field
            adb_utils.send_tab(device_serial)
            time.sleep(2)

            # Send TAB again to make sure we are on the right field
            adb_utils.send_tab(device_serial)

            time.sleep(2)
            adb_utils.send_tab(device_serial)
            time.sleep(2)
            
            # Type the email directly
            if input_text(device_serial, email):
                time.sleep(1)
                # Confirm with the Enter key
                try:
                    adb_utils.press_enter(device_serial)
                    time.sleep(5)  # Wait for refresh
                    return True
                except Exception as e:
                    logger.error(f"❌ Error during confirmation: {e}")
                    return False
        return False

    elif state == "PASSWORD_FIELD":
        # Type the password directly (the keyboard appears automatically)
        if password:
            # Type the password directly
            if input_text(device_serial, password):
                time.sleep(1)
                # Simulate the Enter key to confirm
                try:
                    adb_utils.press_enter(device_serial)
                    time.sleep(5)  # Wait for refresh
                    return True
                except Exception as e:
                    logger.error(f"❌ Error during confirmation: {e}")
                    return False
        return False

    elif state == "LEVEL_UP_EXPERIENCE":
        # Click "Not now"
        result = tap_element(device_serial, text="Not now")
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "ACCOUNT_ALREADY_EXISTS":
        # Click back or NEXT to continue
        result = tap_element(device_serial, text="NEXT") or press_back(device_serial)
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "CHECKING_INFO":
        # Wait for the verification to complete
        logger.info(f"⏳ [{device_serial}] Waiting for verification...")
        time.sleep(5)
        return True

    elif state == "WELCOME_GOOGLE_PLAY":
        # Click "Get started" or "Not now"
        result = tap_element(device_serial, text="Not now") or tap_element(device_serial, text="Get started")
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "LOCAL_RECOMMENDATIONS":
        # Send 3 TABs then Enter to confirm "No thanks"
        adb_utils.send_tab(device_serial)
        time.sleep(1)
        adb_utils.send_tab(device_serial)
        time.sleep(1)
        adb_utils.send_tab(device_serial)
        time.sleep(1)
        try:
            adb_utils.press_enter(device_serial)
            time.sleep(5)  # Wait for refresh
            return True
        except Exception as e:
            logger.error(f"❌ Error during confirmation: {e}")
            return False

    elif state == "SKIP_NEVER_LOOSE_CONTACT":
        # Click skip or no thanks
        result = tap_element(device_serial, text="Skip") or tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "MAKE_SURE_ALWAYS_LOGIN":
        # Click skip or no thanks
        result = tap_element(device_serial, text="Skip") or tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "GOOGLE_BACK_UP":
        # Reset and reopen the Play Store
        download_2.reset_open_play_store(device_serial)
        time.sleep(5)
        return True

    elif state == "ADDITIONAL_SEARCH_SERVICE":
        # Click skip or no
        result = tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "GOOGLE_OPTIMIZED":
        # Click no thanks
        result = tap_element(device_serial, text="No thanks") or press_back(device_serial)
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    elif state == "AGREE_TERMS":
        # Click "I agree"
        result = tap_element(device_serial, text="I agree", class_name="android.widget.Button")
        if result:
            time.sleep(5)  # Wait for refresh
        return result

    return False


# ==========================================================
# =================== MAIN PIPELINE =======================
# ==========================================================

def manage_PS(device_serial: str, email: str = None, password: str = None,
              max_attempts: int = 15, verbose: bool = True) -> bool:
    """
    Manages the Play Store configuration pipeline until NORMAL_UI state is reached.

    Args:
        device_serial: Emulator serial (e.g. "emulator-5554")
        email: Google account email (optional)
        password: Google account password (optional)
        max_attempts: Maximum number of attempts (default: 15)
        verbose: Display detailed logs

    Returns:
        True if successful (NORMAL_UI reached), False otherwise
    """
    logger.info(f"🚀 [{device_serial}] Starting Play Store pipeline")

    attempt = 0
    current_state = None
    none_state_count = 0  # Counter for consecutive None states

    download_2.reset_open_play_store(device_serial)
    time.sleep(15)
    while attempt < max_attempts:
        attempt += 1
        logger.info(f"🔄 [{device_serial}] Play Store check: {attempt}/{max_attempts}")

        download_2.updateUITree(device_serial)
        time.sleep(5)
        # 1. Check the current state
        current_state = verif_status_PS(device_serial, verbose=verbose)

        if current_state is None:
            none_state_count += 1
            logger.error(f"❌ [{device_serial}] Unable to retrieve state (attempt {none_state_count}/2)")

            if none_state_count >= 2:
                logger.error(f"❌ [{device_serial}] Failure: unable to retrieve state 2 consecutive times")
                Database.update_emulator_status(device_serial, "PLAY_STORE_ERROR_UNKNOWN_STATUS")
                return False

            time.sleep(15)
            continue
        else:
            # Reset the counter when a valid state is retrieved
            none_state_count = 0

        # 2. Update the status in the DB
        Database.update_emulator_status(device_serial, current_state)

        # 3. Check whether the goal has been reached
        if current_state == "NORMAL_UI":
            logger.info(f"✅ [{device_serial}] Play Store configured successfully!")
            Database.update_emulator_status(device_serial, "PLAY_STORE_OK")
            return True

        # 4. Check for unknown state - PAUSE FOR HUMAN INSPECTION
        if current_state == "UNKNOWN":
            logger.error(f"❌ [{device_serial}] Failure: unknown state - PAUSE FOR HUMAN INSPECTION")
            Database.update_emulator_status(device_serial, "PAUSED_UNKNOWN_STATE")
            return False

        # 5. Execute the appropriate action
        action_success = handle_state_action(device_serial, current_state, email, password)

        if action_success:
            logger.info(f"✅ [{device_serial}] Action executed successfully")
        else:
            logger.error(f"❌ [{device_serial}] Failure: action failed for state {current_state} - PAUSE FOR INSPECTION")
            Database.update_emulator_status(device_serial, "PAUSED_ACTION_FAILED")
            return False

        # 6. Wait before the next check (the sleep is already in handle_state_action)
        time.sleep(2)

    # If we reach here, max_attempts has been exceeded - PAUSE FOR INSPECTION
    logger.error(f"❌ [{device_serial}] Failure: attempt limit reached ({max_attempts}) - Last state: {current_state}")
    logger.warning(f"⏸️ [{device_serial}] PAUSE FOR HUMAN INSPECTION")
    Database.update_emulator_status(device_serial, "PAUSED_MAX_ATTEMPTS")
    return False


# ==========================================================
# =================== USAGE EXAMPLE =======================
# ==========================================================

if __name__ == "__main__":
    import accounts_manager
    # Usage example
    serial = "emulator-5554"
    #Database.truncate_all_tables()
    #Database.add_emulator(serial, "PS")
    #Database.add_account("u2348857978@gmail.com", "raderbay.frere")
    #Database.link_account_to_emulator("u2348857978@gmail.com", serial)
    time.sleep(10)
    # 1. Make sure an account is linked (assign one if necessary)
    account = accounts_manager.get_emulator_account(serial)
    if not account:
        logger.info(f"🔑 [{serial}] No account linked, assigning one...")
        account = accounts_manager.assign_account_to_emulator(serial)

        if not account:
            logger.error(f"❌ [{serial}] No account available, waiting...")

    # 2. Check the Play Store state before taking an app
    download_2.updateUITree(serial)
    time.sleep(2)
    ps_state = verif_status_PS(serial, verbose=False)

    # 3. If the Play Store is not in NORMAL_UI, handle it with manage_PS
    if not ps_state or ps_state != "NORMAL_UI":
        logger.info(f"🔧 [{serial}] Play Store state: {ps_state}, handling with manage_PS...")

        # ALWAYS pass the account to manage_PS
        success = manage_PS(
            device_serial=serial,
            email=account['email'],
            password=account['mdp'],
            max_attempts=15,
            verbose=False
        )

        if not success:
            logger.error(f"❌ [{serial}] Play Store management failed, waiting...")
            Database.update_emulator_status(serial, "ERROR_MANAGING_PLAY_STORE")
            time.sleep(10)

        
