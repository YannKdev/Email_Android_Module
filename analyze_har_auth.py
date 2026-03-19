#!/usr/bin/env python3
"""
Analyze HAR files to find login/auth requests and explain why test@gmail.com was not captured.
"""

import json
import base64
import urllib.parse
import re
import sys
from pathlib import Path

APPS = [
    {
        "app": "com.skiplagged",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.skiplagged/capture_all.har",
        "url_pattern": "/api/login.php",
    },
    {
        "app": "com.retainquranapp",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.retainquranapp/capture_all.har",
        "url_pattern": "/api/login",
    },
    {
        "app": "com.tigerspike.newlook",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.tigerspike.newlook/capture_all.har",
        "url_pattern": "/v2/users/login",
    },
    {
        "app": "com.vtechnology.mykara",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.vtechnology.mykara/capture_all.har",
        "url_pattern": "/4.1/SignIn.php",
    },
    {
        "app": "com.appspot.scruffapp",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.appspot.scruffapp/capture_all.har",
        "url_pattern": "/app/account/register",
    },
    {
        "app": "com.a101kapida.android",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.a101kapida.android/capture_all.har",
        "url_pattern": "TOKEN/auth",
    },
    {
        "app": "com.mapmyride.android2",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.mapmyride.android2/capture_all.har",
        "url_pattern": ["oauth/authorize", "oauth/token"],
    },
    {
        "app": "com.mapmywalk.android2",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.mapmywalk.android2/capture_all.har",
        "url_pattern": ["oauth/authorize", "oauth/token"],
    },
    {
        "app": "com.armut.armutha",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.armut.armutha/capture_all.har",
        "url_pattern": "oauth2/token",
    },
    {
        "app": "net.eightcard",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/net.eightcard/capture_all.har",
        "url_pattern": "oauth/token.json",
    },
    {
        "app": "net.roamler",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/net.roamler/capture_all.har",
        "url_pattern": "/elysium/token",
    },
    {
        "app": "com.everyplate.android",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.everyplate.android/capture_all.har",
        "url_pattern": "auth/token",
    },
    {
        "app": "com.online.AndroidManorama",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.online.AndroidManorama/capture_all.har",
        "url_pattern": "/token",
    },
    {
        "app": "io.mewtant.pixaiart",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/io.mewtant.pixaiart/capture_all.har",
        "url_pattern": "auth/discord",
    },
    {
        "app": "by.tut.jobs.android",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/by.tut.jobs.android/capture_all.har",
        "url_pattern": ["auth/availability", "account/login"],
    },
    {
        "app": "com.vtechnology.mykara (Google SSO)",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.vtechnology.mykara/capture_all.har",
        "url_pattern": "accounts.google.com/signin",
    },
    {
        "app": "com.platovpn.vpn",
        "har": "/home/ubuntu/dev_osint/pipeline_osint/magisk_frida_analyze/results/com.platovpn.vpn/capture_all.har",
        "url_pattern": "sign_in",
    },
]


def url_matches(url: str, pattern) -> bool:
    if isinstance(pattern, list):
        return any(p.lower() in url.lower() for p in pattern)
    return pattern.lower() in url.lower()


def decode_body(text: str, mime: str) -> str:
    """Try to prettify/decode the body."""
    if not text:
        return "(empty)"
    # Try base64 decode
    try:
        decoded = base64.b64decode(text)
        # Check if it looks like printable text
        try:
            result = decoded.decode("utf-8")
            return f"[BASE64-DECODED]: {result[:500]}"
        except Exception:
            return f"[BASE64-BINARY]: {decoded[:100]!r}"
    except Exception:
        pass
    # Try JSON pretty print
    if "json" in mime.lower() or text.strip().startswith("{") or text.strip().startswith("["):
        try:
            obj = json.loads(text)
            return json.dumps(obj, indent=2, ensure_ascii=False)[:500]
        except Exception:
            pass
    # Try URL decode
    if "urlencoded" in mime.lower() or ("=" in text and "&" in text):
        try:
            pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
            if pairs:
                return "URL-ENCODED:\n" + "\n".join(f"  {k}={v}" for k, v in pairs)
        except Exception:
            pass
    return text[:500]


def search_har_for_string(entries: list, needle: str) -> list:
    """Search entire HAR entries for a string, return matching URLs."""
    matches = []
    needle_lower = needle.lower()
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        # Serialize entry to JSON string for full search
        entry_str = json.dumps(entry, ensure_ascii=False).lower()
        if needle_lower in entry_str:
            matches.append(url)
    return matches


def analyze_app(app_cfg: dict):
    app_name = app_cfg["app"]
    har_path = app_cfg["har"]
    url_pattern = app_cfg["url_pattern"]

    print("=" * 80)
    print(f"APP: {app_name}")
    print(f"HAR: {har_path}")
    print(f"Looking for URL pattern: {url_pattern}")
    print("=" * 80)

    # Load HAR
    try:
        with open(har_path, "r", encoding="utf-8", errors="replace") as f:
            har = json.load(f)
    except FileNotFoundError:
        print(f"  ERROR: HAR file not found: {har_path}")
        print()
        return
    except json.JSONDecodeError as e:
        print(f"  ERROR: Invalid JSON in HAR file: {e}")
        print()
        return

    entries = har.get("log", {}).get("entries", [])
    print(f"Total entries in HAR: {len(entries)}")

    # Search for gmail/test@gmail.com anywhere
    gmail_hits = search_har_for_string(entries, "gmail")
    test_email_hits = search_har_for_string(entries, "test@gmail.com")
    print(f"Entries containing 'gmail': {len(gmail_hits)}")
    if gmail_hits:
        for h in gmail_hits[:5]:
            print(f"  -> {h[:120]}")
    print(f"Entries containing 'test@gmail.com': {len(test_email_hits)}")
    if test_email_hits:
        for h in test_email_hits[:5]:
            print(f"  -> {h[:120]}")

    # Find matching entries
    matched = [e for e in entries if url_matches(e.get("request", {}).get("url", ""), url_pattern)]
    print(f"Entries matching URL pattern: {len(matched)}")

    if not matched:
        print(f"\n  >> NO matching entries found for pattern '{url_pattern}'")
        # Show all unique URLs to help debug
        all_urls = sorted(set(e.get("request", {}).get("url", "") for e in entries))
        print(f"  All unique URLs in HAR ({len(all_urls)} total):")
        for u in all_urls[:40]:
            print(f"    {u[:120]}")
        if len(all_urls) > 40:
            print(f"    ... and {len(all_urls)-40} more")
        print()
        return

    for i, entry in enumerate(matched):
        req = entry.get("request", {})
        resp = entry.get("response", {})

        print(f"\n--- Match {i+1} ---")
        print(f"  Method: {req.get('method', '?')}")
        print(f"  URL: {req.get('url', '?')}")

        # Headers
        headers = req.get("headers", [])
        print(f"  Request Headers ({len(headers)}):")
        for h in headers:
            name = h.get("name", "")
            value = h.get("value", "")
            # Redact Authorization tokens partially
            if name.lower() in ("authorization", "cookie"):
                value = value[:60] + "..." if len(value) > 60 else value
            print(f"    {name}: {value}")

        # Post data
        post_data = req.get("postData", {})
        if post_data:
            mime = post_data.get("mimeType", "")
            text = post_data.get("text", "")
            params = post_data.get("params", [])
            print(f"  Post Data MIME: {mime}")
            if params:
                print(f"  Post Params: {params}")
            if text:
                decoded = decode_body(text, mime)
                print(f"  Post Body:\n{decoded}")
            else:
                print("  Post Body: (empty text)")
        else:
            print("  Post Data: NONE (no body)")

        # Response
        status = resp.get("status", "?")
        status_text = resp.get("statusText", "")
        print(f"  Response Status: {status} {status_text}")

        resp_headers = resp.get("headers", [])
        print(f"  Response Headers ({len(resp_headers)}):")
        for h in resp_headers[:10]:
            print(f"    {h.get('name','')}: {h.get('value','')[:100]}")

        resp_content = resp.get("content", {})
        resp_mime = resp_content.get("mimeType", "")
        resp_text = resp_content.get("text", "")
        resp_encoding = resp_content.get("encoding", "")
        print(f"  Response MIME: {resp_mime}")
        if resp_text:
            if resp_encoding == "base64":
                try:
                    decoded_resp = base64.b64decode(resp_text).decode("utf-8", errors="replace")
                    print(f"  Response Body (base64-decoded, first 500):\n{decoded_resp[:500]}")
                except Exception:
                    print(f"  Response Body (base64, raw first 200): {resp_text[:200]}")
            else:
                decoded_resp = decode_body(resp_text, resp_mime)
                print(f"  Response Body (first 500):\n{decoded_resp}")
        else:
            print("  Response Body: (empty)")

    print()


def main():
    for app_cfg in APPS:
        analyze_app(app_cfg)


if __name__ == "__main__":
    main()
