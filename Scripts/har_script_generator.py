"""
har_script_generator.py

Génère un script Python standalone qui rejoue le flow extrait par har_flow_extractor,
teste plusieurs emails et détecte si l'API révèle l'existence d'un compte email.

Usage (intégration pipeline):
    flow, anchor_idx = extract_flow("capture_all.har")
    generate_replay_script(package_id, flow, anchor_idx)
    should_flag = run_flow_analysis(flow, anchor_idx)
    if should_flag:
        Database.set_request_auto(package_id)
"""

import json
import os
import re
import urllib.parse
import logging
from datetime import datetime
from typing import Optional

from har_flow_extractor import _extract_tokens_from_request, MIN_TOKEN_LENGTH

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

ORIGINAL_EMAIL   = "test@gmail.com"
CONTROL_EMAIL    = "jshdfgqjsdhfgsihdjgfsdf7387dejhf@gmail.com"
TEST_EMAILS      = ["test@gmail.com", "admin@gmail.com", "john@gmail.com"]
ALL_EMAILS       = TEST_EMAILS + [CONTROL_EMAIL]
PASSWORD         = "passwordY#1A"
REQUEST_TIMEOUT  = 15  # secondes

RESULTS_SCRIPT_DIR = "results_script"


# ─── Token plan (pré-calcul) ──────────────────────────────────────────────────

def _find_json_path(value: str, json_text: str) -> Optional[str]:
    """Trouve le chemin JSON (dot-notation) d'une valeur dans un texte JSON."""
    try:
        obj = json.loads(json_text)
        return _find_path_recursive(obj, value, "")
    except (json.JSONDecodeError, ValueError):
        return None


def _find_path_recursive(obj, target: str, path: str) -> Optional[str]:
    if isinstance(obj, str):
        if target == obj or target in obj:
            return path or "_root_"
    elif isinstance(obj, dict):
        for k, v in obj.items():
            result = _find_path_recursive(v, target, f"{path}.{k}" if path else k)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            result = _find_path_recursive(v, target, f"{path}[{i}]" if path else f"[{i}]")
            if result is not None:
                return result
    return None


def _get_by_path(obj, path: str):
    """Navigue un objet JSON avec un chemin dot-notation (ex: 'authToken.token')."""
    if path == "_root_":
        return obj
    for part in re.split(r'\.|\[(\d+)\]', path):
        if not part:
            continue
        if part.isdigit():
            if isinstance(obj, list) and int(part) < len(obj):
                obj = obj[int(part)]
            else:
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return str(obj) if obj is not None else None


def _compute_token_plan(flow_entries: list[dict]) -> list[dict]:
    """
    Pré-calcule le plan de propagation des tokens entre requêtes.

    Retourne une liste de dépendances :
        {
            producer_index: int,   # index dans flow_entries dont la réponse produit le token
            consumer_index: int,   # index dans flow_entries qui utilise le token en requête
            original_value: str,   # valeur du token dans le HAR capturé
            json_path: str|None,   # chemin pour extraire la NOUVELLE valeur depuis la réponse
        }
    """
    plan = []
    seen = set()  # (producer_index, original_value) pour éviter les doublons

    for consumer_idx in range(1, len(flow_entries)):
        tokens = _extract_tokens_from_request(flow_entries[consumer_idx])

        for token in tokens:
            for producer_idx in range(consumer_idx - 1, -1, -1):
                resp_text = (
                    flow_entries[producer_idx]
                    .get("response", {})
                    .get("content", {})
                    .get("text", "") or ""
                )
                if token not in resp_text:
                    continue

                key = (producer_idx, token)
                if key in seen:
                    break
                seen.add(key)

                json_path = _find_json_path(token, resp_text)
                plan.append({
                    "producer_index": producer_idx,
                    "consumer_index": consumer_idx,
                    "original_value": token,
                    "json_path": json_path,
                })
                break  # un seul producteur par token

    return plan


# ─── Exécution in-process ─────────────────────────────────────────────────────

def _replace_email_in_text(text: str, new_email: str) -> str:
    """Remplace l'email original (et ses variantes encodées) dans un texte."""
    if not text:
        return text
    result = text
    result = result.replace(ORIGINAL_EMAIL, new_email)
    result = result.replace(
        urllib.parse.quote(ORIGINAL_EMAIL),
        urllib.parse.quote(new_email)
    )
    # Base64 (rare mais possible)
    import base64
    orig_b64 = base64.b64encode(ORIGINAL_EMAIL.encode()).decode()
    new_b64  = base64.b64encode(new_email.encode()).decode()
    result = result.replace(orig_b64, new_b64)
    return result


def _normalize_response(text: str, email: str) -> str:
    """
    Normalise une réponse pour la comparaison (supprime les valeurs dynamiques
    propres à la session mais conserve la structure sémantique).
    """
    if not text:
        return ""
    normalized = text
    # Supprimer l'email lui-même
    normalized = normalized.replace(email, "__EMAIL__")
    normalized = normalized.replace(urllib.parse.quote(email), "__EMAIL__")
    # Supprimer les timestamps Unix (10-13 chiffres)
    normalized = re.sub(r'\b\d{10,13}\b', '__TS__', normalized)
    # Supprimer les UUIDs
    normalized = re.sub(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        '__UUID__', normalized, flags=re.IGNORECASE
    )
    # Supprimer les JWT (3 segments base64 séparés par des points)
    normalized = re.sub(
        r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
        '__JWT__', normalized
    )
    # Supprimer les tokens hex longs (≥ 24 chars)
    normalized = re.sub(r'[0-9a-f]{24,}', '__HEX__', normalized, flags=re.IGNORECASE)
    return normalized


def _replay_flow_for_email(
    flow_entries: list[dict],
    anchor_flow_index: int,
    token_plan: list[dict],
    email: str,
) -> Optional[tuple[int, str]]:
    """
    Rejoue le flow complet pour un email donné.
    Propage les tokens dynamiques entre requêtes.

    Retourne (status_code, normalized_body) de la requête anchor, ou None si erreur.
    """
    import requests as req_lib

    # token_map: valeur_originale → valeur_courante (mise à jour après chaque réponse)
    token_map: dict[str, str] = {}

    anchor_result = None

    for i, entry in enumerate(flow_entries):
        if entry.get("_tls_failed"):
            continue

        request = entry.get("request", {})
        url     = request.get("url", "")
        method  = request.get("method", "GET")
        headers = {h["name"]: h["value"] for h in request.get("headers", [])}
        body    = request.get("postData", {}).get("text", "") or ""

        # Appliquer le token_map (substitutions issues des réponses précédentes)
        for orig, current in token_map.items():
            url     = url.replace(orig, current)
            body    = body.replace(orig, current)
            headers = {k: v.replace(orig, current) for k, v in headers.items()}

        # Sur l'anchor : substituer l'email (et le mot de passe si présent)
        if entry.get("_is_anchor"):
            url     = _replace_email_in_text(url, email)
            body    = _replace_email_in_text(body, email)
            headers = {k: _replace_email_in_text(v, email) for k, v in headers.items()}
            # Substitution du mot de passe
            for pwd_key in ('"password"', "'password'", "password="):
                if pwd_key in body:
                    # Remplacer la valeur après la clé (JSON ou form-encoded)
                    body = re.sub(
                        r'("password"\s*:\s*")[^"]*(")',
                        r'\g<1>' + PASSWORD + r'\g<2>',
                        body
                    )
                    body = re.sub(
                        r'(password=)[^&\s]*',
                        r'\g<1>' + urllib.parse.quote(PASSWORD),
                        body
                    )
                    break

        # Nettoyer Content-Length (recalculé par requests)
        headers.pop("content-length", None)
        headers.pop("Content-Length", None)

        try:
            response = req_lib.request(
                method=method,
                url=url,
                headers=headers,
                data=body.encode("utf-8") if body else None,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
        except Exception as exc:
            logger.debug(f"[flow_replay] Erreur requête [{i}] {url[:60]}: {exc}")
            if entry.get("_is_anchor"):
                return None
            continue

        resp_text = response.text or ""

        # Mettre à jour le token_map depuis cette réponse
        for dep in token_plan:
            if dep["producer_index"] != i:
                continue
            orig_val  = dep["original_value"]
            json_path = dep["json_path"]
            if not json_path:
                continue
            try:
                resp_json = response.json()
                new_val = _get_by_path(resp_json, json_path)
                if new_val and new_val != orig_val and len(new_val) >= MIN_TOKEN_LENGTH:
                    token_map[orig_val] = new_val
            except (ValueError, TypeError):
                # Réponse non-JSON : recherche brute de la valeur dans le texte
                if orig_val in resp_text:
                    # Trouver la nouvelle valeur au même endroit (heuristique : même longueur)
                    pattern = re.escape(orig_val)
                    # On garde l'original si on ne peut pas extraire la nouvelle valeur
                    pass

        # Capturer la réponse de l'anchor
        if entry.get("_is_anchor"):
            anchor_result = (response.status_code, _normalize_response(resp_text, email))

    return anchor_result


def run_flow_analysis(
    flow_entries: list[dict],
    anchor_flow_index: int,
    verbose: bool = True,
) -> bool:
    """
    Teste 4 emails sur le flow et détecte si l'API révèle l'existence d'un compte.

    Logique :
    - Rejouer le flow pour chaque email (test + contrôle)
    - Comparer (status_code, corps normalisé) des emails test vs le contrôle
    - Si au moins un email test donne une réponse DIFFÉRENTE du contrôle → TRUE

    Retourne True si l'API distingue les emails (request_auto doit être mis à TRUE).
    """
    token_plan = _compute_token_plan(flow_entries)

    if verbose:
        logger.info(f"[flow_analysis] Plan tokens : {len(token_plan)} dépendance(s)")

    results: dict[str, Optional[tuple[int, str]]] = {}

    for email in ALL_EMAILS:
        result = _replay_flow_for_email(flow_entries, anchor_flow_index, token_plan, email)
        results[email] = result
        if verbose:
            if result:
                logger.info(f"[flow_analysis] {email[:30]:<30} → status={result[0]}")
            else:
                logger.info(f"[flow_analysis] {email[:30]:<30} → ERREUR (pas de réponse anchor)")

    control_result = results.get(CONTROL_EMAIL)
    if control_result is None:
        logger.warning("[flow_analysis] Impossible d'obtenir une réponse pour le contrôle, abandon")
        return False

    # Comparer chaque email test contre le contrôle
    for email in TEST_EMAILS:
        test_result = results.get(email)
        if test_result is None:
            continue
        status_diff = test_result[0] != control_result[0]
        body_diff   = test_result[1] != control_result[1]
        if status_diff or body_diff:
            if verbose:
                logger.info(
                    f"[flow_analysis] ✅ Différence détectée pour '{email}' vs contrôle "
                    f"(status={status_diff}, body={body_diff}) → request_auto=TRUE"
                )
            return True

    if verbose:
        logger.info("[flow_analysis] Aucune différence détectée → request_auto non modifié")
    return False


# ─── Génération du script standalone ─────────────────────────────────────────

def generate_replay_script(
    package_id: str,
    flow_entries: list[dict],
    anchor_flow_index: int,
    output_dir: str = RESULTS_SCRIPT_DIR,
) -> str:
    """
    Génère un script Python standalone dans output_dir/{package_id}_replay.py.
    Retourne le chemin du fichier généré.
    """
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{package_id}_replay.py")

    token_plan = _compute_token_plan(flow_entries)

    # Sérialiser les données à embarquer (corriger les booléens JSON → Python)
    flow_json      = json.dumps(flow_entries,  indent=2, ensure_ascii=False)
    flow_json      = flow_json.replace('"_is_anchor": true', '"_is_anchor": True') \
                              .replace('"_is_anchor": false', '"_is_anchor": False')
    plan_json      = json.dumps(token_plan,    indent=2, ensure_ascii=False)
    generated_at   = datetime.now().isoformat(timespec="seconds")

    script = f'''#!/usr/bin/env python3
"""
Script de replay email-enumeration pour : {package_id}
Généré le : {generated_at}

Ce script rejoue le flow HTTP capturé, teste plusieurs emails et détecte si
l'API révèle l'existence d'un compte (comparaison de réponses).

Usage:
    python {package_id}_replay.py [--update-db]

Options:
    --update-db   Met à jour request_auto=TRUE en base si détection positive
                  (nécessite un fichier .env avec les credentials DB)
"""

import json
import os
import re
import sys
import urllib.parse
import base64
import logging
from typing import Optional

try:
    import requests
except ImportError:
    print("[!] Module 'requests' manquant : pip install requests")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

PACKAGE_ID     = {json.dumps(package_id)}
ORIGINAL_EMAIL = "test@gmail.com"
CONTROL_EMAIL  = "jshdfgqjsdhfgsihdjgfsdf7387dejhf@gmail.com"
TEST_EMAILS    = ["test@gmail.com", "admin@gmail.com", "john@gmail.com"]
ALL_EMAILS     = TEST_EMAILS + [CONTROL_EMAIL]
PASSWORD       = "passwordY#1A"
ANCHOR_INDEX   = {anchor_flow_index}
TIMEOUT        = 15  # secondes par requête

# ─── Flow et plan de propagation des tokens ───────────────────────────────────

FLOW_ENTRIES = {flow_json}

TOKEN_PLAN = {plan_json}

# ─── Utilitaires ──────────────────────────────────────────────────────────────

def _get_by_path(obj, path: str) -> Optional[str]:
    """Navigue un objet JSON via chemin dot-notation."""
    if not path or path == "_root_":
        return str(obj) if obj is not None else None
    for part in re.split(r'\\.|\\[(\\d+)\\]', path):
        if not part:
            continue
        if part.isdigit():
            if isinstance(obj, list) and int(part) < len(obj):
                obj = obj[int(part)]
            else:
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return str(obj)


def _replace_email(text: str, new_email: str) -> str:
    if not text:
        return text
    result = text.replace(ORIGINAL_EMAIL, new_email)
    result = result.replace(urllib.parse.quote(ORIGINAL_EMAIL), urllib.parse.quote(new_email))
    orig_b64 = base64.b64encode(ORIGINAL_EMAIL.encode()).decode()
    new_b64  = base64.b64encode(new_email.encode()).decode()
    result = result.replace(orig_b64, new_b64)
    return result


def _normalize(text: str, email: str) -> str:
    if not text:
        return ""
    s = text
    s = s.replace(email, "__EMAIL__").replace(urllib.parse.quote(email), "__EMAIL__")
    s = re.sub(r\'\\b\\d{{10,13}}\\b\', \'__TS__\', s)
    s = re.sub(r\'[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}\',
               \'__UUID__\', s, flags=re.IGNORECASE)
    s = re.sub(r\'eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\', \'__JWT__\', s)
    s = re.sub(r\'[0-9a-f]{{24,}}\', \'__HEX__\', s, flags=re.IGNORECASE)
    return s


# ─── Replay ───────────────────────────────────────────────────────────────────

def replay_for_email(email: str) -> Optional[tuple]:
    """Rejoue le flow complet pour un email, retourne (status, normalized_body) de l\'anchor."""
    token_map: dict[str, str] = {{}}
    anchor_result = None

    for i, entry in enumerate(FLOW_ENTRIES):
        if entry.get("_tls_failed"):
            continue

        req     = entry.get("request", {{}})
        url     = req.get("url", "")
        method  = req.get("method", "GET")
        headers = {{h["name"]: h["value"] for h in req.get("headers", [])}}
        body    = req.get("postData", {{}}).get("text", "") or ""

        # Appliquer les tokens dynamiques des réponses précédentes
        for orig, cur in token_map.items():
            url     = url.replace(orig, cur)
            body    = body.replace(orig, cur)
            headers = {{k: v.replace(orig, cur) for k, v in headers.items()}}

        # Sur l\'anchor : remplacer email + password
        if entry.get("_is_anchor"):
            url     = _replace_email(url, email)
            body    = _replace_email(body, email)
            headers = {{k: _replace_email(v, email) for k, v in headers.items()}}
            body = re.sub(r\'("password"\\s*:\\s*")[^"]*(")\', r\'\\g<1>\' + PASSWORD + r\'\\g<2>\', body)
            body = re.sub(r\'(password=)[^&\\s]*\', r\'\\g<1>\' + urllib.parse.quote(PASSWORD), body)

        headers.pop("content-length", None)
        headers.pop("Content-Length", None)

        try:
            resp = requests.request(
                method=method, url=url, headers=headers,
                data=body.encode("utf-8") if body else None,
                timeout=TIMEOUT, allow_redirects=True,
            )
        except Exception as e:
            logger.warning(f"  [{{i}}] Erreur : {{e}}")
            if entry.get("_is_anchor"):
                return None
            continue

        # Mettre à jour token_map depuis la réponse
        for dep in TOKEN_PLAN:
            if dep["producer_index"] != i or not dep.get("json_path"):
                continue
            try:
                new_val = _get_by_path(resp.json(), dep["json_path"])
                if new_val and new_val != dep["original_value"] and len(new_val) >= 10:
                    token_map[dep["original_value"]] = new_val
            except (ValueError, TypeError):
                pass

        if entry.get("_is_anchor"):
            anchor_result = (resp.status_code, _normalize(resp.text, email))
            logger.info(f"  Anchor → status={{resp.status_code}} body={{resp.text[:120]}}")

    return anchor_result


# ─── Analyse ──────────────────────────────────────────────────────────────────

def run_analysis() -> bool:
    """Retourne True si l\'API distingue les emails (fuite d\'information)."""
    results = {{}}
    for email in ALL_EMAILS:
        logger.info(f"\\n[TEST] {{email}}")
        results[email] = replay_for_email(email)

    control = results.get(CONTROL_EMAIL)
    if control is None:
        logger.warning("Contrôle sans réponse, impossible de conclure.")
        return False

    logger.info("\\n" + "=" * 60)
    logger.info("COMPARAISON vs contrôle")
    logger.info("=" * 60)
    detected = False
    for email in TEST_EMAILS:
        res = results.get(email)
        if res is None:
            logger.info(f"  {{email:<35}} → pas de réponse")
            continue
        status_diff = res[0] != control[0]
        body_diff   = res[1] != control[1]
        flag = "✅ DIFFÉRENT" if (status_diff or body_diff) else "  identique"
        logger.info(f"  {{email:<35}} → {{flag}} (status={{status_diff}}, body={{body_diff}})")
        if status_diff or body_diff:
            detected = True

    logger.info("=" * 60)
    logger.info(f"Résultat : {{'API EXPOSE existence email → request_auto=TRUE' if detected else 'Pas de fuite détectée'}}")
    return detected


# ─── DB update (optionnel) ────────────────────────────────────────────────────

def update_db():
    """Met à jour request_auto=TRUE pour ce package (nécessite psycopg2 + .env)."""
    try:
        from dotenv import load_dotenv
        import psycopg2
        load_dotenv()
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE packages_full_pipeline SET request_auto = TRUE WHERE package_id = %s;",
                (PACKAGE_ID,)
            )
        conn.commit()
        conn.close()
        logger.info(f"[DB] request_auto=TRUE mis à jour pour {{PACKAGE_ID}}")
    except Exception as e:
        logger.error(f"[DB] Erreur mise à jour : {{e}}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    update_db_flag = "--update-db" in sys.argv
    detected = run_analysis()
    if detected and update_db_flag:
        update_db()
    sys.exit(0 if not detected else 1)
'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(script)

    logger.info(f"[generator] Script généré : {output_path}")
    return output_path
