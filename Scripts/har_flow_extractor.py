"""
har_flow_extractor.py

Extrait le flow minimal nécessaire pour rejouer une requête email depuis un capture_all.har.
Algorithme purement rétroactif : remonte la chaîne de dépendances token par token.

Usage:
    flow = extract_flow("capture_all.har", email="test@gmail.com")
    save_flow_har(flow, "flow.har")
"""

import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional


# ─── Constantes ───────────────────────────────────────────────────────────────

SEARCH_TERMS = [
    "test@gmail.com",
    "test%40gmail.com",
    "dGVzdEBnbWFpbC5jb20=",
    "1aedb8d9dc4751e229a335e371db8058",
    "87924606b4131a8aceeeae8868531fbb9712aaa07a5d3a756b26ce0f5d6ca674",
]

# Tokens trop courts ou trop communs pour être des identifiants significatifs
MIN_TOKEN_LENGTH = 10

# Headers dont la valeur est toujours statique (inutiles pour la corrélation)
SKIP_HEADERS = {
    "content-type", "accept", "accept-encoding", "accept-language",
    "content-length", "user-agent", "connection", "host",
    "cache-control", "pragma", "transfer-encoding",
}

# Valeurs à ignorer même si longues (faux positifs courants)
SKIP_VALUES = {
    "application/json", "application/x-www-form-urlencoded",
    "text/plain", "text/html", "gzip, deflate", "true", "false",
    "android", "okhttp", "retrofit", "HTTP/1.1", "HTTP/2",
}

# Domaines à exclure de l'analyse (analytics, monitoring — pas de vrais tokens)
NOISE_DOMAINS = {
    "google.com", "googleapis.com", "gstatic.com",
    "facebook.com", "fbcdn.net",
    "crashlytics.com", "firebase.com",
    "amplitude.com", "appsflyer",
    "connectivitycheck",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_noise_entry(entry: dict) -> bool:
    """Ignore les entrées TLS failed et les domaines analytics."""
    if entry.get("_tls_failed"):
        return True
    url = entry.get("request", {}).get("url", "")
    return any(domain in url for domain in NOISE_DOMAINS)


def _get_all_text(entry: dict) -> str:
    """Concatène tout le texte d'une entrée (url + req body + resp body + headers)."""
    parts = []
    req = entry.get("request", {})
    resp = entry.get("response", {})

    parts.append(req.get("url", ""))
    parts.append(req.get("postData", {}).get("text", "") or "")
    parts.append(resp.get("content", {}).get("text", "") or "")

    for h in req.get("headers", []):
        parts.append(h.get("value", ""))
    for h in resp.get("headers", []):
        parts.append(h.get("value", ""))

    return " ".join(parts)


def _flatten_json_values(obj, depth=0) -> list[str]:
    """Extrait récursivement toutes les valeurs string d'un objet JSON."""
    if depth > 6:
        return []
    results = []
    if isinstance(obj, dict):
        for v in obj.values():
            results.extend(_flatten_json_values(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_flatten_json_values(item, depth + 1))
    elif isinstance(obj, str):
        results.append(obj)
    return results


def _looks_like_token(value: str) -> bool:
    """Heuristique : est-ce que cette valeur ressemble à un token/identifiant dynamique ?"""
    if len(value) < MIN_TOKEN_LENGTH:
        return False
    if value in SKIP_VALUES:
        return False
    # Doit contenir au moins quelques caractères alphanum
    alnum = sum(c.isalnum() for c in value)
    if alnum < 6:
        return False
    # Éviter les URLs complètes (trop génériques)
    if value.startswith("http://") or value.startswith("https://"):
        return False
    # Éviter les chemins de fichiers
    if value.startswith("/") and value.count("/") > 2:
        return False
    return True


def _extract_tokens_from_request(entry: dict) -> set[str]:
    """Extrait les valeurs intéressantes de la REQUÊTE d'une entrée HAR."""
    tokens = set()
    req = entry.get("request", {})

    # Headers suspects (Authorization, Cookie, X-Token, etc.)
    for h in req.get("headers", []):
        name = h.get("name", "").lower()
        value = h.get("value", "")
        if name in SKIP_HEADERS:
            continue
        if not _looks_like_token(value):
            continue
        tokens.add(value)
        # Cas "Bearer <token>" → extraire le token seul aussi
        if value.lower().startswith("bearer "):
            tokens.add(value[7:])

    # Query string params
    for qs in req.get("queryString", []):
        value = qs.get("value", "")
        if _looks_like_token(value):
            tokens.add(value)
            tokens.add(urllib.parse.unquote(value))

    # Body
    body_text = req.get("postData", {}).get("text", "") or ""
    mime = req.get("postData", {}).get("mimeType", "") or ""

    if body_text:
        # Tenter JSON
        try:
            body_json = json.loads(body_text)
            for val in _flatten_json_values(body_json):
                if _looks_like_token(val):
                    tokens.add(val)
        except (json.JSONDecodeError, ValueError):
            pass

        # Tenter URL-encoded
        if "form" in mime or "urlencoded" in mime:
            try:
                decoded = urllib.parse.unquote(body_text)
                parsed = urllib.parse.parse_qs(decoded)
                for vals in parsed.values():
                    for val in vals:
                        # Tenter de parser la valeur comme JSON (body imbriqué ex: e=[{...}])
                        try:
                            inner = json.loads(val)
                            for v in _flatten_json_values(inner):
                                if _looks_like_token(v):
                                    tokens.add(v)
                        except (json.JSONDecodeError, ValueError):
                            # Pas du JSON : ajouter la valeur brute si pertinente
                            if _looks_like_token(val):
                                tokens.add(val)
            except Exception:
                pass

        # Recherche brute de patterns UUID / hex longs
        uuids = re.findall(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            body_text, re.IGNORECASE
        )
        tokens.update(uuids)

        hex_tokens = re.findall(r'[0-9a-f]{24,}', body_text, re.IGNORECASE)
        tokens.update(t for t in hex_tokens if _looks_like_token(t))

    # Filtrer les tokens qui sont les search terms eux-mêmes (l'email n'est pas un token à remonter)
    tokens -= set(SEARCH_TERMS)

    return tokens


def _response_contains_token(entry: dict, token: str) -> bool:
    """Vérifie si la RÉPONSE d'une entrée contient un token donné."""
    resp = entry.get("response", {})
    content_text = resp.get("content", {}).get("text", "") or ""

    if token in content_text:
        return True

    # Vérifier aussi les response headers (Set-Cookie, etc.)
    for h in resp.get("headers", []):
        if token in h.get("value", ""):
            return True

    # Vérifier version URL-decoded
    decoded = urllib.parse.unquote(token)
    if decoded != token and decoded in content_text:
        return True

    return False


# ─── Algorithme principal ──────────────────────────────────────────────────────

def find_anchor(entries: list[dict], email: Optional[str] = None) -> Optional[int]:
    """
    Trouve l'index de la requête contenant l'email.
    Utilise les SEARCH_TERMS par défaut, ou un email custom.
    """
    terms = SEARCH_TERMS.copy()
    if email and email not in terms:
        terms.insert(0, email)
        terms.insert(1, urllib.parse.quote(email))

    for i, entry in enumerate(entries):
        if entry.get("_tls_failed"):
            continue
        req = entry.get("request", {})
        body = req.get("postData", {}).get("text", "") or ""
        url = req.get("url", "") or ""
        resp_body = entry.get("response", {}).get("content", {}).get("text", "") or ""

        for term in terms:
            if term in body or term in url or term in resp_body:
                return i

    return None


def extract_flow(
    har_path: str,
    email: Optional[str] = None,
    anchor_index: Optional[int] = None,
    verbose: bool = True,
) -> list[dict]:
    """
    Extrait le flow minimal depuis capture_all.har.

    Algorithme rétroactif récursif :
      1. Trouver la requête email (anchor)
      2. Extraire ses tokens
      3. Pour chaque token, trouver dans les réponses précédentes celui qui l'a produit
      4. Répéter récursivement sur chaque prédécesseur trouvé
      5. Retourner les entrées dans l'ordre chronologique

    Args:
        har_path: Chemin vers capture_all.har
        email: Email custom (optionnel, utilise SEARCH_TERMS par défaut)
        anchor_index: Forcer un index anchor (utile pour tests)
        verbose: Afficher les logs

    Returns:
        Liste ordonnée d'entrées HAR formant le flow minimal
    """
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)

    entries = har["log"]["entries"]

    if verbose:
        print(f"[flow] HAR chargé : {len(entries)} entrées totales")

    # 1. Trouver l'anchor
    if anchor_index is None:
        anchor_index = find_anchor(entries, email)

    if anchor_index is None:
        print("[flow] ERREUR : requête email introuvable dans le HAR")
        return []

    anchor = entries[anchor_index]
    if verbose:
        print(f"[flow] Anchor trouvé à l'index {anchor_index} : "
              f"{anchor['request']['method']} {anchor['request']['url'][:70]}")

    # 2. Algorithme BFS/DFS rétroactif
    included_indices: set[int] = {anchor_index}
    queue: list[int] = [anchor_index]
    visited_tokens: set[str] = set()

    while queue:
        current_idx = queue.pop(0)
        current_entry = entries[current_idx]

        # Extraire les tokens de la requête courante
        tokens = _extract_tokens_from_request(current_entry)
        new_tokens = tokens - visited_tokens
        visited_tokens |= new_tokens

        if verbose and new_tokens:
            print(f"  [{current_idx}] {len(new_tokens)} tokens extraits "
                  f"(ex: {list(new_tokens)[:2]})")

        # Chercher en arrière qui a produit ces tokens
        for token in new_tokens:
            for i in range(current_idx - 1, -1, -1):
                if i in included_indices:
                    continue
                if _is_noise_entry(entries[i]):
                    continue
                if _response_contains_token(entries[i], token):
                    if verbose:
                        url = entries[i]['request']['url'][:60]
                        print(f"    → Token '{token[:20]}...' produit par [{i}] {url}")
                    included_indices.add(i)
                    queue.append(i)  # remonter récursivement
                    break  # un seul producteur par token suffit (le plus récent)

    # 3. Trier par ordre chronologique (index croissant = ordre HAR = ordre temporel)
    flow_indices = sorted(included_indices)
    anchor_flow_index = flow_indices.index(anchor_index)

    # Marquer l'anchor dans le flow (copie pour ne pas muter l'original)
    flow = []
    for i, idx in enumerate(flow_indices):
        entry = dict(entries[idx])
        if idx == anchor_index:
            entry["_is_anchor"] = True
        flow.append(entry)

    if verbose:
        print(f"\n[flow] Flow extrait : {len(flow)} requêtes sur {len(entries)} totales")
        for i, idx in enumerate(flow_indices):
            e = entries[idx]
            marker = " ← EMAIL" if idx == anchor_index else ""
            print(f"  {i+1}. [{idx}] {e['request']['method']} {e['request']['url'][:65]}{marker}")

    return flow, anchor_flow_index


def find_anchor_in_flow(flow_entries: list[dict]) -> int:
    """Retourne l'index de l'anchor dans le flow (marqué _is_anchor=True)."""
    for i, e in enumerate(flow_entries):
        if e.get("_is_anchor"):
            return i
    return len(flow_entries) - 1  # fallback : dernière entrée


# ─── Sauvegarde ───────────────────────────────────────────────────────────────

def save_flow_har(flow: list[dict], output_path: str) -> None:
    """Sauvegarde le flow extrait en fichier HAR valide."""
    har_output = {
        "log": {
            "version": "1.2",
            "creator": {
                "name": "har_flow_extractor",
                "version": "1.0"
            },
            "entries": flow
        }
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(har_output, f, indent=2, ensure_ascii=False)
    print(f"[flow] Flow sauvegardé : {output_path} ({len(flow)} entrées)")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python har_flow_extractor.py <capture_all.har> [email] [output_flow.har]")
        sys.exit(1)

    har_path = sys.argv[1]
    email = sys.argv[2] if len(sys.argv) > 2 else None
    output = sys.argv[3] if len(sys.argv) > 3 else "flow.har"

    flow = extract_flow(har_path, email=email, verbose=True)

    if flow:
        save_flow_har(flow, output)
