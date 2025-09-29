#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disney Speedstorm → Discord webhook notifier (PocketGamer).
- Scrapes active codes from: https://www.pocketgamer.com/disney-speedstorm/codes/
- Persists 'seen' codes in a JSON file.
- Posts ONE webhook message per newly discovered active code.

Env:
  WEBHOOK_URL_CODEX   -> Discord webhook URL (required)
  DRY_RUN=true             -> don't post, just print (optional)

Usage:
  python speedstorm_codes_scraper.py
  # or on Windows PowerShell:
  #   $env:WEBHOOK_URL_CODEX='https://discord.com/api/webhooks/...'
  #   python .\speedstorm_codes_scraper.py
"""

import json
import os
import re
from pathlib import Path
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.pocketgamer.com/disney-speedstorm/codes/"
STATE_PATH = Path("speedstorm_codes_state.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "speedstorm-codes/1.0 (+discord-webhook)"})


def fetch_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def extract_codes(html: str) -> List[Dict]:
    """
    Extract active codes from the PocketGamer article.
    We look for list items/paragraphs that contain a CODE token and optional reward text.
    Returns list of dicts: {code, reward, expires, source_line}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Heuristics:
    # - Most pages list codes as bullet points or short paragraphs.
    # - Code shape: uppercase letters/digits, ~6–16 chars. Examples: M1SSP1GGY3, M4DT34P4RTY, PRIDE2025
    CODE_RE = re.compile(r"\b[A-Z0-9]{6,16}\b")
    EXPIRED_HINTS = re.compile(r"\b(expired|inactive|ended)\b", re.I)

    # Gather candidate text blocks
    blocks = []
    for tag in soup.find_all(["li", "p", "div"]):
        txt = tag.get_text(" ", strip=True)
        if txt and len(txt) <= 300:
            blocks.append(txt)

    items: List[Dict] = []
    for line in blocks:
        if EXPIRED_HINTS.search(line or ""):
            continue  # skip obvious expired lines

        # find codes in the line
        codes = [m.group(0) for m in CODE_RE.finditer(line)]
        if not codes:
            continue

        # PocketGamer often formats like: "M1SSP1GGY3 - 3 Miss Piggy Shards (new!)"
        # Split reward on the first dash/colon if present.
        reward = None
        m = re.search(r"\b[A-Z0-9]{6,16}\b\s*[-–—:]\s*(.+)$", line)
        if m:
            reward = m.group(1).strip()

        # Sometimes they show an explicit expiry date in text.
        exp = None
        m2 = re.search(r"(?:valid|expires?|until)\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})", line, re.I)
        if m2:
            exp = m2.group(1).strip()

        for c in codes:
            items.append({
                "code": c,
                "reward": reward,
                "expires": exp,
                "source_line": line,
            })

    # De-dupe by code, keep the first occurrence & trim noise
    seen = set()
    deduped = []
    for it in items:
        if it["code"] in seen:
            continue
        seen.add(it["code"])
        deduped.append(it)

    return deduped


def load_state() -> Dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: Dict):
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def post_webhook(webhook_url: str, content: str) -> Optional[str]:
    if os.getenv("DRY_RUN", "false").lower() == "true":
        print("[DRY_RUN] Would POST:", content)
        return "DRY_RUN_MSG_ID"
    r = SESSION.post(
        f"{webhook_url}?wait=true",
        headers={"Content-Type": "application/json"},
        json={"content": content},
        timeout=30,
    )
    if r.status_code in (200, 204):
        try:
            return r.json().get("id")
        except Exception:
            return None
    else:
        print(f"[WARN] Webhook POST failed: {r.status_code} {r.text[:200]}")
        return None


def format_discord_message(item: Dict) -> str:
    parts = [f"**Disney Speedstorm — New Code Found!**", f"`{item['code']}`"]
    if item.get("reward"):
        parts.append(f"**Reward:** {item['reward']}")
    if item.get("expires"):
        parts.append(f"**Expires:** {item['expires']}")
    parts.append(f"<{PAGE_URL}>")
    return "\n".join(parts)


def main():
    webhook = (os.getenv("WEBHOOK_URL_CODEX") or "").strip()
    if not webhook:
        raise SystemExit("Missing WEBHOOK_URL_CODEX env var.")

    html = fetch_html(PAGE_URL)
    items = extract_codes(html)

    state = load_state()
    seen_codes = set(state.get("seen_codes", []))

    new_items = [it for it in items if it["code"] not in seen_codes]

    # Only announce NEW codes
    for it in new_items:
        content = format_discord_message(it)
        mid = post_webhook(webhook, content)
        if mid:
            print(f"[OK] Announced new code {it['code']} (message id={mid})")
        else:
            print(f"[WARN] Failed to announce code {it['code']}")

    # Persist union of seen codes
    if new_items:
        seen_codes.update(it["code"] for it in new_items)
        state["seen_codes"] = sorted(seen_codes)
        save_state(state)
    else:
        print("[Info] No new codes.")

if __name__ == "__main__":
    main()
