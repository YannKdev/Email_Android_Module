import subprocess
import os
from PIL import Image
from io import BytesIO
import re
import xml.etree.ElementTree as ET
import json
import time

# Import config for ADB path
try:
    import config
    ADB_BINARY = config.ADB_BINARY
except ImportError:
    ADB_BINARY = "adb"  # Fallback if config is not available

def get_foreground_package(device_id: str) -> str:
    """
    Returns the package name of the currently foreground app.
    """
    try:
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "dumpsys", "activity", "activities"],
            capture_output=True,
            text=True,
            check=True
        )
        # Look for "mResumedActivity" or "topResumedActivity" pattern
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
    Checks if Chrome is in the foreground.
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
    #print(f"▶️ App launched: {package_name} on {serial}")

def uninstall_app(serial, package_name):
    subprocess.run(
        f"{ADB_BINARY} -s {serial} uninstall {package_name}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False   # some apps return a warning
    )
    #print(f"🗑 App uninstalled: {package_name} on {serial}")


def uninstall_all_third_party_packages(serial):
    """
    Retrieves the list of third-party packages installed on a device
    and uninstalls them all.

    Args:
        serial (str): The device or emulator ID (adb devices).

    Returns:
        list: List of uninstalled packages.
    """
    packages = get_installed_packages(serial)
    uninstalled = []
    total = len(packages)

    print(f"🧹 [{serial}] {total} third-party packages to uninstall...")
    for i, package in enumerate(packages, 1):
        try:
            print(f"🗑️ [{serial}] ({i}/{total}) uninstalling {package}")
            subprocess.run(
                f"{ADB_BINARY} -s {serial} uninstall {package}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30
            )
            uninstalled.append(package)
            time.sleep(2)  # Reduced from 5s to 2s
        except subprocess.TimeoutExpired:
            print(f"⚠️ [{serial}] Uninstall timeout for {package}, skipping")
        except Exception as e:
            print(f"[ERROR] Failed to uninstall {package}: {e}")

    print(f"✅ [{serial}] {len(uninstalled)}/{total} packages uninstalled")
    return uninstalled


def stop_app(serial, package_name):
    subprocess.run(
        f"{ADB_BINARY} -s {serial} shell am force-stop {package_name}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )
    #print(f"⏹ App stopped: {package_name} on {serial}")


_PROTECTED_PACKAGES = {
    "com.google.android.gms",
    "com.android.vending",
}

def get_installed_packages(serial, timeout=30):
    """
    Returns the set of installed packages (third-party apps only)
    on the emulator / device identified by its adb serial.
    Packages in _PROTECTED_PACKAGES (microG) are excluded from the result.
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
        print(f"⚠️ [{serial}] Timeout on pm list packages, returning empty list")
        return set()
import subprocess
def adb_long_tap(x, y, device_id, duration_ms=2000):
    """
    Simulates a long tap on an Android device via ADB.

    Args:
        x (int): X coordinate of the tap point.
        y (int): Y coordinate of the tap point.
        duration_ms (int, optional): Duration of the long tap in milliseconds. Default 2000.
        device_id (str, optional): Device ID for adb -s. Default None.
    """
    cmd = [ADB_BINARY]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)]
    
    try:
        subprocess.run(cmd, check=True)
        #print(f"Long tap performed at ({x},{y}) for {duration_ms}ms on {device_id or 'default device'}")
    except subprocess.CalledProcessError as e:
        print(f"Error during long tap: {e}")
def disable_android_animations(device_id: str):
    """
    Disables all Android animations on a specific device via ADB.

    Args:
        device_id (str): The device or emulator ID (adb devices).
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
    Dumps the UI of the target device and retrieves it locally.

    Args:
        device_id (str): The device or emulator ID (adb devices)
    """
    if(path == None):
        path = "temp/"+device_id+"/ui.xml"
    # Dump UI on the device
    subprocess.run(
        [ADB_BINARY, "-s", device_id, "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Retrieve the XML file locally
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
        # remove null keys
        el = {k: v for k, v in el.items() if v not in (None, "", False)}  # remove null keys
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
    Converts the UI XML to JSON.

    Args:
        device_id: Device ID
        text_only: If True, keeps only elements with non-empty text/content-desc
                   (token optimization for calls without screenshot)
    """
    input_path="temp/"+device_id+"/ui.xml"
    output_path="temp/"+device_id+"/ui.json"
    tree = ET.parse(input_path)
    root = tree.getroot()

    elements = []
    flatten_node(device_id, root, elements)
    elements = deduplicate(elements)

    # Filter to keep only elements with text (token optimization)
    # EditText fields are always kept even if empty (form fields without placeholder)
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
    Checks if frida-server is running, and starts it if not.
    Returns True if frida-server is running, False otherwise.
    """
    MAX_ATTEMPTS = 3

    def is_frida_running():
        """Checks if frida-server is running."""
        result = subprocess.run(
            f'{ADB_BINARY} -s {device_id} shell "ps -A | grep frida-server"',
            capture_output=True,
            text=True,
            shell=True
        )
        output = result.stdout.strip()
        # If "frida-server" is found in the output (and not just "grep"), it's running
        return "frida-server" in output and "grep" not in output

    def start_frida_server():
        """Attempts to start frida-server. Returns (success, error_msg)."""
        # Start frida-server in the background with nohup
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
            # Timeout can be normal while the server is starting
            return True, None

        # Si pas d'erreur, c'est bon
        if not output:
            return True, None

        # "Address already in use" error - zombie frida-server
        if "Address already in use" in output:
            return False, "ADDRESS_IN_USE"

        # Autre erreur
        return False, output

    def kill_frida_server():
        """Kills frida-server."""
        subprocess.run(
            f'{ADB_BINARY} -s {device_id} shell "pkill frida-server"',
            capture_output=True,
            shell=True
        )
        time.sleep(2)

    # Always kill frida-server before restarting to start from a clean state
    if is_frida_running():
        print(f"[Frida] frida-server running on {device_id}, kill + restart...")
        kill_frida_server()

    # Retry loop
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"[Frida] Attempt {attempt}/{MAX_ATTEMPTS} to start on {device_id}...")

        success, error = start_frida_server()

        if success:
            time.sleep(3)  # Allow time for the server to start
            if is_frida_running():
                print(f"[Frida] ✅ frida-server started successfully on {device_id}")
                return True

        if error == "ADDRESS_IN_USE":
            print(f"[Frida] ⚠️ Port already in use, killing zombie process...")
            kill_frida_server()
            continue
        elif error:
            print(f"[Frida] ❌ Erreur: {error}")

        time.sleep(2)

    # Failed after all attempts
    print(f"[Frida] ❌ Failed to start frida-server on {device_id} after {MAX_ATTEMPTS} attempts")
    return False

def adb_scroll_half_screen(device_id=None, duration_ms=300):
    """
    Scrolls down half the screen via ADB.

    Args:
        device_id (str, optional): Device ID. Default None.
        duration_ms (int): Swipe duration in ms.
    """
    # Get screen resolution
    cmd_size = [ADB_BINARY]
    if device_id:
        cmd_size += ["-s", device_id]
    cmd_size += ["shell", "wm", "size"]
    
    try:
        output = subprocess.check_output(cmd_size).decode()
        # Extract width and height: e.g. "Physical size: 1080x2400"
        size_str = output.strip().split(":")[1].strip()
        width, height = map(int, size_str.split("x"))
        
        # Swipe coordinates
        x = width // 2
        y_start = height * 0.7  # 1/4 of the screen (swipe start)
        y_end = height * 0.3    # half of the screen (swipe end)
        
        cmd_swipe = [ADB_BINARY]
        if device_id:
            cmd_swipe += ["-s", device_id]
        cmd_swipe += ["shell", "input", "swipe",
                      str(x), str(y_start), str(x), str(y_end), str(duration_ms)]
        
        subprocess.run(cmd_swipe, check=True)
        #print(f"Half-screen downward swipe performed on {device_id or 'default device'}")
        
    except subprocess.CalledProcessError as e:
        print(f"ADB error: {e}")

def adb_back(device_id=None):
    """
    Simulates the 'Back' button on an Android device via ADB.

    Args:
        device_id (str, optional): Device ID for adb -s. Default None.
    """
    cmd = [ADB_BINARY]
    if device_id:
        cmd += ["-s", device_id]
    cmd += ["shell", "input", "keyevent", "KEYCODE_BACK"]
    
    try:
        subprocess.run(cmd, check=True)
        #print(f"Back button sent on {device_id or 'default device'}")
    except subprocess.CalledProcessError as e:
        print(f"Error sending back button: {e}")
        
def take_snapshot(device_id :str, screenshot:bool=False, text_only:bool=False):
    """
    Takes a UI snapshot (and optionally a screenshot).

    Args:
        device_id: Device ID
        screenshot: If True, also takes a screenshot
        text_only: If True, the JSON will only contain elements with text
                   (token optimization for calls without screenshot)
    """
    if(screenshot):
        take_android_screenshot(device_id=device_id)
    take_ui_xml(device_id=device_id)
    clean_ui_xml(device_id, text_only=text_only)

def take_android_screenshot(device_id: str):
    """
    Takes a screenshot of a specific Android device and saves it as JPEG.
    Overwrites the file if it already exists.

    Args:
        device_id (str): The device or emulator ID (adb devices).
        file_path (str): Full path to the JPEG file to create.
    """
    file_path = "temp/"+device_id+"/screenshot.jpeg"
    # Ensure the directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # ADB command to capture the screen (PNG output)
    adb_screencap_cmd = [ADB_BINARY, "-s", device_id, "exec-out", "screencap", "-p"]
    
    try:
        # Retrieve the screenshot in memory
        result = subprocess.run(adb_screencap_cmd, stdout=subprocess.PIPE, check=True)
        png_data = result.stdout
        
        # Convert PNG to JPEG
        image = Image.open(BytesIO(png_data))

        image = image.convert("RGB")
        image.save(
            file_path,
            "JPEG",
            quality=60,
            optimize=True,
            progressive=True
        )

        #print(f"✅ Screenshot of {device_id} saved to: {file_path}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] ADB error during capture on {device_id}: {e}")
    except Exception as e:
        print(f"[ERROR] Error saving screenshot: {e}")


def tap(device_id: str, x: int, y: int):
    """
    Simulates a tap on a specific Android device screen at coordinates (x, y).

    Args:
        device_id (str): The device or emulator ID (adb devices).
        x (int): Horizontal coordinate.
        y (int): Vertical coordinate.
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "tap", str(x), str(y)],
            check=True
        )
        #print(f"✅ Tap performed on {device_id} at ({x}, {y})")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] ADB error on {device_id}: failed to tap ({x}, {y}) - {e}")

def type_text(device_id: str, text: str):
    """
    Simulates text input on a specific Android device.
    Handles special characters # and @.

    Args:
        device_id (str): The device or emulator ID (adb devices).
        text (str): Text to type.
    """
    for char in text:
        try:
            if char == " ":
                # ADB replaces spaces with %s
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
            print(f"[ERROR] ADB error on {device_id} while typing '{char}': {e}")

def is_keyboard_active(device_id: str) -> bool:
    """
    Checks if the virtual keyboard is currently displayed on the device.

    Args:
        device_id (str): The device or emulator ID (adb devices).

    Returns:
        bool: True if the keyboard is visible, False otherwise.
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
    Hides the virtual keyboard on a specific Android device if visible.

    Args:
        device_id (str): The device or emulator ID (adb devices).
    """
    try:
        # Send KEYCODE_BACK to dismiss the keyboard
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "4"],
            check=True
        )
        #print(f"✅ Keyboard hidden on {device_id} (if visible)")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] ADB error on {device_id}: failed to hide keyboard - {e}")

def send_tab(device_id: str):
    """
    Sends the TAB key on a specific Android device.

    Args:
        device_id (str): The device or emulator ID (adb devices).
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "61"],
            check=True
        )
        # print(f"✅ TAB key sent on {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] ADB error on {device_id}: failed to send TAB - {e}")
        
def press_enter(device_id: str):
    """
    Simulates the Enter key on a specific Android device.

    Args:
        device_id (str): The device or emulator ID (adb devices).
    """
    try:
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "keyevent", "66"],
            check=True
        )
        #print(f"✅ Enter key simulated on {device_id}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] ADB error on {device_id}: failed to press Enter - {e}")

        
def dismiss_not_responding_popup(device_id: str) -> bool:
    """
    Checks if an "App not responding" popup is displayed and clicks "Wait" if present.

    Args:
        device_id (str): The device or emulator ID (adb devices).

    Returns:
        bool: True if a popup was detected and dismissed, False otherwise.
    """
    try:
        # Fast method: check via dumpsys if an ANR window is displayed
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

        # Dump UI on the device (increased timeout as the system may be slow)
        try:
            subprocess.run(
                [ADB_BINARY, "-s", device_id, "shell", "uiautomator", "dump", "/sdcard/ui_check.xml"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30  # Timeout increased to 30s as System UI can be very slow
            )
        except subprocess.TimeoutExpired:
            # If timeout and ANR window was detected, try clicking at a known position
            if has_anr_window:
                print(f"⚠️ [{device_id}] uiautomator timeout but ANR detected, attempting blind click")
                # Typical position of the "Wait" button on most emulators
                subprocess.run(
                    [ADB_BINARY, "-s", device_id, "shell", "input", "tap", "360", "716"],
                    check=False,
                    timeout=5
                )
                time.sleep(0.5)
                return True
            raise

        # Retrieve XML content directly
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "cat", "/sdcard/ui_check.xml"],
            capture_output=True,
            text=True,
            timeout=15
        )

        xml_content = result.stdout.strip()
        if not xml_content:
            return False

        # Parse XML
        root = ET.fromstring(xml_content)

        # Patterns to detect the "not responding" popup (multi-language)
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

        # First check if this is indeed a "not responding" popup
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

        # Find button to click (priority: Wait > Close app > OK)
        # Possible resource-ids for the "Wait" button
        wait_resource_ids = ['android:id/aerr_wait', 'android:id/button2']
        # Possible resource-ids for the "Close" button (fallback)
        close_resource_ids = ['android:id/aerr_close', 'android:id/button1']
        # Possible button texts
        wait_texts = ['wait', 'attendre', 'ok']
        close_texts = ['close', 'close app', 'fermer', 'ok']

        button_to_click = None

        # First search by resource-id (more reliable)
        for node in root.iter('node'):
            resource_id = node.attrib.get('resource-id', '')
            if resource_id in wait_resource_ids:
                button_to_click = node
                break

        # If not found by resource-id, search by text (if popup confirmed)
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

        # Fallback: look for Close button if Wait not found (if popup confirmed)
        if button_to_click is None and is_not_responding_popup:
            for node in root.iter('node'):
                resource_id = node.attrib.get('resource-id', '')
                if resource_id in close_resource_ids:
                    button_to_click = node
                    break

            # Last resort: search Close by text
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

        # Extract bounds and compute center
        bounds = button_to_click.attrib.get('bounds', '')
        if not bounds:
            return False

        # Format: "[left,top][right,bottom]" (unchanged, it's a format spec)
        match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds)
        if not match:
            return False

        left, top, right, bottom = map(int, match.groups())
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2

        # Click the button
        subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "input", "tap", str(center_x), str(center_y)],
            check=True,
            timeout=5
        )

        button_text = button_to_click.attrib.get('text', 'unknown')
        print(f"⚠️ [{device_id}] 'Not responding' popup detected and closed ('{button_text}' clicked)")
        return True

    except subprocess.TimeoutExpired:
        print(f"⚠️ [{device_id}] Timeout during popup check")
        return False
    except ET.ParseError:
        # Invalid XML, no popup
        return False
    except Exception as e:
        print(f"⚠️ [{device_id}] Error checking popup: {e}")
        return False


def get_emulator_size(device_id, adb_path="adb", retries=5, delay=3):
    """
    Returns (width, height) of the Android emulator.
    Retries on transient errors (e.g. WM service not yet ready after a crash).
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
                raise RuntimeError("Failed to read emulator resolution")
            width, height = map(int, match.groups())
            return width, height
        except (subprocess.CalledProcessError, RuntimeError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(delay)

    raise RuntimeError(f"get_emulator_size failed after {retries} attempts: {last_error}")

def close_all_apps(device_id: str):
    """
    Returns to the home screen and kills all background apps.
    Should be called between analyses to clean up residual apps.
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
        print(f"[{device_id}] Error in close_all_apps: {e}")


def disable_airplane_mode_if_on(device_id: str) -> bool:
    """
    Checks if airplane mode is active and disables it if necessary.
    Returns True if airplane mode was active (and has been disabled).
    """
    try:
        result = subprocess.run(
            [ADB_BINARY, "-s", device_id, "shell", "settings", "get", "global", "airplane_mode_on"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == "1":
            print(f"[{device_id}] Airplane mode detected — disabling...")
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
            print(f"[{device_id}] Airplane mode disabled")
            return True
        return False
    except Exception as e:
        print(f"[{device_id}] Error in disable_airplane_mode_if_on: {e}")
        return False


def android_has_internet(device_id: str) -> bool:
    """
    Checks if the Android emulator has internet access.

    Args:
        device_id (str): The device or emulator ID (adb devices).

    Returns:
        bool: True if connected to the internet, False otherwise.
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