import base64
import json
import logging
import os
from openai import OpenAI
from pathlib import Path
from dotenv import load_dotenv
import adb_utils
import Database

load_dotenv()

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2


PROMPTS_DIR = Path("/home/ubuntu/dev_osint/pipeline_osint/analyze_ressources_v2/Prompts")


def load_prompt(file_path: str) -> str:
    path = PROMPTS_DIR / file_path
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def analyze_login_entry(
    device_id,
    add_screenshot: bool = False,
    package_name: str = None,
    iteration: int = None,
    max_iterations: int = None,
    already_tapped: list = None,
    same_screen_count: int = 0,
) -> dict:
    """
    Retourne :
    {
      "etat": "MODALS" | "OTHER" | "NO_LOGIN" | "NO_EMAIL_LOGIN" | "LOGIN_EMAIL" | "NEED_SCREENSHOT",
      "where_tap": {...} | null
    }

    Si add_screenshot=False et que l'IA a besoin d'un screenshot,
    elle retourne NEED_SCREENSHOT. L'appelant doit alors relancer avec add_screenshot=True.

    iteration / max_iterations / already_tapped : contexte de navigation transmis à l'IA
    pour éviter les boucles et détecter plus tôt l'absence de login email.
    """
    screen_path = "temp/" + device_id + "/screenshot.jpeg"
    adb_utils.take_snapshot(device_id, screenshot=add_screenshot, text_only=not add_screenshot)

    with open("temp/" + device_id + "/ui.json", 'r', encoding='utf-8') as fichier:
        data = fichier.read()

    # Injecter le contexte de navigation en tête du payload si disponible
    if iteration is not None and already_tapped is not None:
        nav_context = json.dumps({
            "__nav_context__": {
                "iteration": iteration,
                "max_iterations": max_iterations,
                "already_tapped": already_tapped,
                "same_screen_count": same_screen_count,
            }
        })
        user_content = nav_context + "\n\n" + data
    else:
        user_content = data

    if add_screenshot:
        result = call_openai_image_json(
            system_prompt=load_prompt("XML_Image/First_step.txt"),
            user_content=user_content,
            image_path=screen_path,
            package_name=package_name
        )
    else:
        result = call_openai_text_json(
            system_prompt=load_prompt("XML_Only/First_step.txt"),
            user_content=user_content,
            package_name=package_name
        )

    etat = result.get("etat")
    if etat not in {"MODALS", "OTHER", "NO_LOGIN", "NO_EMAIL_LOGIN", "LOGIN_EMAIL", "NEED_SCREENSHOT"}:
        raise ValueError(f"Etat invalide: {etat}")

    if etat in ("NO_LOGIN", "NO_EMAIL_LOGIN"):
        dump_path = os.path.join("temp", device_id, "no_login_last_ui.json")
        try:
            with open(dump_path, "w", encoding="utf-8") as _f:
                _f.write(data)
            logger.info(f"[{device_id}] {etat} — ui.json sauvegardé dans {dump_path}")
        except Exception as _e:
            logger.warning(f"[{device_id}] {etat} — impossible de sauvegarder ui.json : {_e}")

    where_tap = result.get("where_tap")
    if isinstance(where_tap, dict):
        is_back   = where_tap.get("action") == "BACK"
        is_coords = {"x", "y"} <= where_tap.keys()
        if not is_back and not is_coords:
            raise ValueError(f"where_tap invalide: {where_tap}")

    return result


def analyze_login_page(device_id, add_screenshot: bool = False, package_name: str = None) -> dict:
    """
    {
      "etat": "EMAIL_UNIQUE" | "EMAIL_MDP" | "NO_LOGIN",
      "email_field": {...} | null,
      "password_field": {...} | null,
      "submit_button": {...} | "NO_SUBMIT_BUTTON" | null
    }
    """
    if add_screenshot:
        logger.warning("Screenshot non implémenté pour analyze_login_page")
    adb_utils.take_snapshot(device_id, screenshot=False, text_only=True)

    with open("temp/" + device_id + "/ui.json", 'r', encoding='utf-8') as fichier:
        data = fichier.read()

    result = call_openai_text_json(
        system_prompt=load_prompt("XML_Only/Email_login_step.txt"),
        user_content=data,
        package_name=package_name
    )

    if result["etat"] == "NO_LOGIN":
        return result

    if result["email_field"] is None:
        raise ValueError("Login détecté sans champ email")

    return result


def analyze_register_page(device_id, add_screenshot: bool = False, package_name: str = None) -> dict:
    """
    {
        "etat": "EMAIL" | "NO_EMAIL" | "ERROR",
        "email_field": {"x": float, "y": float} | null
    }
    """
    if add_screenshot:
        logger.warning("Screenshot non implémenté pour analyze_register_page")
    adb_utils.take_snapshot(device_id, screenshot=False, text_only=True)

    with open("temp/" + device_id + "/ui.json", 'r', encoding='utf-8') as fichier:
        data = fichier.read()

    return call_openai_text_json(
        system_prompt=load_prompt("XML_Only/Email_register_step.txt"),
        user_content=data,
        package_name=package_name
    )


def analyze_go_to_register_page(device_id, add_screenshot: bool = False, package_name: str = None) -> dict:
    """
    {
      "etat": "PAGE_REGISTER" | "TAP" | "NO_INFO",
      "where_tap": {...} | null
    }
    """
    if add_screenshot:
        logger.warning("Screenshot non implémenté pour analyze_go_to_register_page")
    adb_utils.take_snapshot(device_id, screenshot=False, text_only=True)

    with open("temp/" + device_id + "/ui.json", 'r', encoding='utf-8') as fichier:
        data = fichier.read()

    return call_openai_text_json(
        system_prompt=load_prompt("XML_Only/Go_to_register_step.txt"),
        user_content=data,
        package_name=package_name
    )


def analyze_email_exists(device_id, add_screenshot: bool = False, package_name: str = None) -> dict:
    """
    {
      "etat": "INFO_EMAIL" | "NO_INFO_EMAIL" | "ERROR"
    }
    """
    if add_screenshot:
        logger.warning("Screenshot non implémenté pour analyze_email_exists")
    adb_utils.take_snapshot(device_id, screenshot=False, text_only=True)

    with open("temp/" + device_id + "/ui.json", 'r', encoding='utf-8') as fichier:
        data = fichier.read()

    result = call_openai_text_json(
        system_prompt=load_prompt("XML_Only/Verify_info_email.txt"),
        user_content=data,
        package_name=package_name
    )

    if result["etat"] not in {"INFO_EMAIL", "NO_INFO_EMAIL", "ERROR"}:
        raise ValueError("Etat email_exists invalide")

    return result


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Accumulateur de tokens par analyse (réinitialisé avant chaque app)
_token_counter = {"input": 0, "output": 0}


def reset_token_counter():
    _token_counter["input"] = 0
    _token_counter["output"] = 0


def get_token_count() -> dict:
    return dict(_token_counter)


class OpenAIJSONError(Exception):
    pass


def _parse_json_response(raw: str) -> dict:
    """Parse et valide la réponse JSON du modèle."""
    raw = raw.strip()
    lines = raw.splitlines()
    if lines and lines[0].strip() == "```json" and lines[-1].strip() == "```":
        lines = lines[1:-1]
    raw = "\n".join(lines)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OpenAIJSONError(f"JSON invalide\n--- RAW ---\n{raw}") from e
    if not isinstance(parsed, dict):
        raise OpenAIJSONError("Le JSON retourné n'est pas un objet")
    return parsed


def call_openai_text_json(
    system_prompt: str,
    user_content: str,
    model: str = "gpt-5-nano",
    package_name: str = None
) -> dict:
    """
    Appelle OpenAI avec séparation system/user et force une réponse JSON.
    Retry automatique jusqu'à _MAX_RETRIES fois sur OpenAIJSONError.
    """
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.responses.create(
                model=model,
                text={"format": {"type": "json_object"}},
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_prompt}]
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_content}]
                    }
                ]
            )
            output_text = response.output_text
            if not output_text:
                raise OpenAIJSONError("Réponse vide du modèle")
            parsed = _parse_json_response(output_text)
            usage = response.usage
            _token_counter["input"] += usage.input_tokens
            _token_counter["output"] += usage.output_tokens
            logger.info(f"OpenAI response: {parsed} | tokens: {usage.input_tokens}in/{usage.output_tokens}out")
            return parsed
        except OpenAIJSONError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                logger.warning(f"OpenAI JSON invalide (tentative {attempt}/{_MAX_RETRIES}), retry... {e}")
    raise last_error


def call_openai_image_json(
    system_prompt: str,
    user_content: str,
    image_path: str,
    model: str = "gpt-4.1-mini",
    package_name: str = None
) -> dict:
    """
    Appelle OpenAI avec séparation system/user + image et force une réponse JSON.
    Retry automatique jusqu'à _MAX_RETRIES fois sur OpenAIJSONError.
    """
    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = client.responses.create(
                model=model,
                text={"format": {"type": "json_object"}},
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_prompt}]
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": user_content},
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{image_base64}",
                            }
                        ]
                    }
                ]
            )
            output_text = response.output_text
            if not output_text:
                raise OpenAIJSONError("Réponse vide du modèle")
            parsed = _parse_json_response(output_text)
            usage = response.usage
            _token_counter["input"] += usage.input_tokens
            _token_counter["output"] += usage.output_tokens
            logger.info(f"OpenAI response: {parsed} | tokens: {usage.input_tokens}in/{usage.output_tokens}out")
            return parsed
        except OpenAIJSONError as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                logger.warning(f"OpenAI JSON invalide (tentative {attempt}/{_MAX_RETRIES}), retry... {e}")
    raise last_error
