#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arknights: Endfield -> Discord webhook notifier (Game8).
- Scrapes active codes from: https://game8.co/games/Arknights-Endfield/archives/571509
- Posts ONE webhook message per newly discovered active code.
- Optional role ping, but **only once per run** (first successfully posted new-code message).
- Weekly health ping to a separate summary webhook when there's no new code for >= 7 days.

Env:
  WEBHOOK_URL_CODEX           -> Discord webhook URL for code alerts (required)
  WEBHOOK_URL_SUMMARY         -> Discord webhook URL for health pings (optional)
  ROLE_ID_ARKNIGHTS_ENDFIELD  -> Discord role ID to @mention on new codes (optional; ping once/run)
  DRY_RUN=true                -> don't post, just print (optional)
"""

import json
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

PAGE_URL = "https://game8.co/games/Arknights-Endfield/archives/571509"
STATE_PATH = Path("arknights_endfield_codes_state.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "arknights-endfield-codes/1.0 (+discord-webhook)"})


def fetch_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _normalize_code(code: str) -> str:
    # Codes are typically case-insensitive but shown uppercase.
    return _clean_text(code).upper()


def _is_placeholder(code_text: str) -> bool:
    # Skip placeholder rows like "None currently", "N/A", etc.
    t = code_text.strip().lower()
    return (not t) or ("none" in t) or (t in {"n/a", "na", "-", "—"})


def _extract_expiration(text: str) -> Optional[str]:
    """
    Extract expiration date from text like:
    - "Expires 1/29/2026"
    - "Only for PC Version Expires 1/29/2026"
    - "Expires TBA"
    """
    # Match "Expires" followed by date or TBA
    match = re.search(r"Expires?\s+(\d{1,2}/\d{1,2}/\d{4}|TBA)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _extract_notes(text: str) -> Optional[str]:
    """
    Extract notes like "(PC Version Only)" or "Only for PC Version".
    """
    notes = []
    # Match variations: "PC Version Only", "Only for PC Version", "PC Only"
    if re.search(r"(PC\s*(Version)?\s*Only|Only\s*(for\s*)?PC(\s*Version)?)", text, re.IGNORECASE):
        notes.append("PC Version Only")
    if re.search(r"(Mobile\s*(Version)?\s*Only|Only\s*(for\s*)?Mobile(\s*Version)?)", text, re.IGNORECASE):
        notes.append("Mobile Version Only")
    return ", ".join(notes) if notes else None


def _find_active_codes_section(soup: BeautifulSoup) -> Optional[Tag]:
    """
    Find the heading containing 'Active' and 'Codes', then return the next table.
    """
    for h in soup.find_all(["h2", "h3", "h4"]):
        heading_text = _clean_text(h.get_text(" ")).lower()
        if "active" in heading_text and "code" in heading_text:
            # Find the next table after this heading
            p = h
            while p:
                p = p.find_next_sibling()
                if not p:
                    break
                if isinstance(p, Tag) and p.name in ("h2", "h3", "h4"):
                    break
                if isinstance(p, Tag) and p.name == "table":
                    return p
                # Sometimes table is nested in a div
                if isinstance(p, Tag):
                    table = p.find("table")
                    if table:
                        return table
    return None


def _extract_code_from_cell(cell: Tag) -> Optional[str]:
    """
    Extract the actual code from a table cell.
    Game8 uses:
    - input.a-clipboard__textInput with value="CODE"
    - data-clipboard-text attribute on copy buttons
    - data-code attribute on elements
    """
    # Game8 specific: look for clipboard input with value attribute
    for elem in cell.find_all("input", class_="a-clipboard__textInput"):
        code = elem.get("value", "").strip()
        if code:
            return code

    # Also check any input with a value attribute
    for elem in cell.find_all("input", attrs={"value": True}):
        code = elem.get("value", "").strip()
        if code and len(code) >= 5:
            return code

    # Try data attributes
    for elem in cell.find_all(attrs={"data-clipboard-text": True}):
        code = elem.get("data-clipboard-text", "").strip()
        if code:
            return code

    for elem in cell.find_all(attrs={"data-code": True}):
        code = elem.get("data-code", "").strip()
        if code:
            return code

    # Fallback: look for code-like patterns in the cell text
    cell_text = cell.get_text(" ")
    code_pattern = re.compile(r'\b([A-Z0-9][A-Z0-9\-]{4,19})\b', re.IGNORECASE)
    matches = code_pattern.findall(cell_text)

    skip_words = {
        "COPY", "COPIED", "REDEEM", "CODES", "CODE", "REWARDS", "EXPIRES",
        "VERSION", "ONLY", "MOBILE", "TBA", "NONE"
    }

    for match in matches:
        upper = match.upper()
        if upper not in skip_words and not upper.startswith("EXPIRE"):
            if not re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', match):
                return match

    return None


def _parse_table(table: Tag) -> List[Dict]:
    """
    Parse Game8 table with columns: Code | Rewards (with expiration inline).
    """
    rows = table.find_all("tr")
    if not rows:
        return []

    items: List[Dict] = []
    for tr in rows:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        # Check if this is a header row
        header_text = _clean_text(tr.get_text(" ")).lower()
        if "redeem" in header_text and "reward" in header_text:
            continue
        if header_text.startswith("code") or header_text.startswith("redeem code"):
            continue

        # First cell: code
        code_cell = cells[0]
        code = _extract_code_from_cell(code_cell)

        if not code:
            continue

        code = _normalize_code(code)
        if _is_placeholder(code):
            continue

        # Second cell: rewards (may include expiration info)
        reward_cell = cells[1]
        reward_text = _clean_text(reward_cell.get_text(" "))

        # Extract expiration from reward text or look in entire row
        full_row_text = _clean_text(tr.get_text(" "))
        expires = _extract_expiration(full_row_text)
        notes = _extract_notes(full_row_text)

        # Clean reward text by removing expiration info and common UI text
        reward_clean = re.sub(r"(Only for )?(PC|Mobile)\s*Version\s*", "", reward_text, flags=re.IGNORECASE)
        reward_clean = re.sub(r"Expires?\s+(\d{1,2}/\d{1,2}/\d{4}|TBA)\s*", "", reward_clean, flags=re.IGNORECASE)
        reward_clean = re.sub(r"\bCopy\b|\bCopied\b", "", reward_clean, flags=re.IGNORECASE)
        reward_clean = _clean_text(reward_clean)

        # Normalize bullet points and formatting
        reward_clean = re.sub(r"[・•]\s*", "", reward_clean)
        reward_clean = re.sub(r"\s+x(\d)", r" x\1", reward_clean)

        items.append({
            "code": code,
            "reward": reward_clean or None,
            "expires": expires,
            "notes": notes,
        })

    return items


def extract_codes(html: str) -> List[Dict]:
    """
    Extract codes from Game8's Arknights: Endfield codes page.
    Returns: list of dicts {code, reward, expires, notes, source_line}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Try to find the active codes table
    table = _find_active_codes_section(soup)
    if table:
        items = _parse_table(table)
    else:
        # Fallback: try all tables on the page
        items = []
        for t in soup.find_all("table"):
            parsed = _parse_table(t)
            items.extend(parsed)

    # Add source_line for debugging
    for it in items:
        parts = [it["code"]]
        if it.get("reward"):
            parts.append(it["reward"])
        if it.get("expires"):
            parts.append(f"Expires {it['expires']}")
        if it.get("notes"):
            parts.append(f"({it['notes']})")
        it["source_line"] = " — ".join(parts)

    # De-dupe by code
    deduped: List[Dict] = []
    seen_codes = set()
    for it in items:
        if it["code"] in seen_codes:
            continue
        seen_codes.add(it["code"])
        deduped.append(it)

    return deduped


def load_state() -> Dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: Dict):
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def post_webhook(webhook_url: str, content: str, retries: int = 4) -> Optional[str]:
    """
    Post to Discord webhook with basic retry/backoff.
    - Handles 429 with Retry-After
    - Retries on 5xx and common transient network errors
    """
    is_dry = (os.getenv("DRY_RUN", "false").lower() == "true")
    if is_dry:
        print("[DRY_RUN] Would POST:", content.replace("\n", " | "))
        return "DRY_RUN_MSG_ID"

    backoff = 1.5
    for attempt in range(1, retries + 1):
        try:
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
            if r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", "1"))
                print(f"[429] Rate limited. Sleeping {retry_after}s...")
                time.sleep(min(retry_after, 10))
            elif 500 <= r.status_code < 600:
                print(f"[{r.status_code}] Discord server error. Attempt {attempt}/{retries}.")
                time.sleep(backoff ** attempt)
            else:
                snippet = r.text[:200].replace("\n", " ")
                print(f"[WARN] Webhook POST failed: {r.status_code} {snippet}")
                return None
        except requests.RequestException as e:
            print(f"[ERR] Network error: {e}. Attempt {attempt}/{retries}.")
            time.sleep(backoff ** attempt)
    return None


def format_new_code_message(item: Dict, role_mention: Optional[str]) -> str:
    parts = []
    if role_mention:
        parts.append(role_mention)
    parts.append("**Arknights: Endfield — New Code Found!**")
    parts.append(f"`{item['code']}`")
    if item.get("reward"):
        parts.append(f"**Rewards:** {item['reward']}")
    if item.get("expires"):
        parts.append(f"**Expires:** {item['expires']}")
    if item.get("notes"):
        parts.append(f"**Note:** {item['notes']}")
    parts.append(f"<{PAGE_URL}>")
    return "\n".join(parts)


def format_health_message(total_seen: int) -> str:
    return (
        "**Arknights: Endfield scraper health check**\n"
        "No new codes today.\n"
        f"Seen codes tracked: **{total_seen}**\n"
        f"Source: <{PAGE_URL}>"
    )


def should_health_ping(state: Dict, now_sp: datetime) -> bool:
    """
    Fire health ping if last ping is >= 7 days ago AND there are no new codes.
    """
    last = state.get("last_health_ping_iso")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(last)
    except Exception:
        return True
    return (now_sp - prev) >= timedelta(days=7)


def main():
    tz = ZoneInfo("America/Sao_Paulo")
    now_sp = datetime.now(tz)

    webhook_codes = (os.getenv("WEBHOOK_URL_CODEX") or "").strip()
    if not webhook_codes:
        raise SystemExit("Missing WEBHOOK_URL_CODEX env var.")
    webhook_summary = (os.getenv("WEBHOOK_URL_SUMMARY") or "").strip()

    role_id_env = (os.getenv("ROLE_ID_ARKNIGHTS_ENDFIELD") or "").strip()
    role_mention_template = f"<@&{role_id_env}>" if role_id_env else None

    html = fetch_html(PAGE_URL)
    items = extract_codes(html)

    print(f"[Info] Found {len(items)} active codes on page.")
    for it in items:
        print(f"  - {it['source_line']}")

    state = load_state()
    seen_codes = set(state.get("seen_codes", []))

    new_items = [it for it in items if it["code"] not in seen_codes]

    # ---- Ping-once-per-run control ----
    ping_available = bool(role_mention_template)

    # Announce NEW codes
    for it in new_items:
        role_for_this_message = role_mention_template if ping_available else None
        content = format_new_code_message(it, role_for_this_message)
        mid = post_webhook(webhook_codes, content)
        if mid:
            print(f"[OK] Announced new code {it['code']} (message id={mid})")
            # Mark that we've used the ping for this run only after a successful send
            if ping_available:
                ping_available = False
        else:
            print(f"[WARN] Failed to announce code {it['code']}")

    # Update state if new codes
    if new_items:
        seen_codes.update(it["code"] for it in new_items)
        state["seen_codes"] = sorted(seen_codes)
        save_state(state)

    # Health ping (only if no new codes and weekly cadence)
    if not new_items and webhook_summary:
        if should_health_ping(state, now_sp):
            msg = format_health_message(total_seen=len(seen_codes))
            mid = post_webhook(webhook_summary, msg)
            if mid:
                print(f"[OK] Health ping sent (message id={mid})")
                state["last_health_ping_iso"] = now_sp.isoformat()
                if os.getenv("DRY_RUN", "false").lower() != "true":
                    save_state(state)
            else:
                print("[WARN] Failed to send health ping.")

    if not new_items:
        print("[Info] No new codes.")


if __name__ == "__main__":
    main()
