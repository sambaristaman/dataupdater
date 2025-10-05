#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Disney Speedstorm → Discord webhook notifier (PocketGamer).
- Scrapes active codes from: https://www.pocketgamer.com/disney-speedstorm/codes/
- Posts ONE webhook message per newly discovered active code.
- Optional role ping, but **only once per run** (first successfully posted new-code message).
- Weekly health ping to a separate summary webhook when there's no new code for ≥ 7 days.

Env:
  WEBHOOK_URL_CODEX   -> Discord webhook URL for Speedstorm alerts (required)
  WEBHOOK_URL_SUMMARY      -> Discord webhook URL for health pings (optional)
  ROLE_ID_SPEEDSTORM       -> Discord role ID to @mention on new codes (optional; ping once/run)
  DRY_RUN=true             -> don't post, just print (optional)
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
from bs4 import BeautifulSoup

PAGE_URL = "https://www.pocketgamer.com/disney-speedstorm/codes/"
STATE_PATH = Path("speedstorm_codes_state.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "speedstorm-codes/1.2 (+discord-webhook)"})


def fetch_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def extract_codes(html: str) -> List[Dict]:
    """
    Extract active codes from the PocketGamer article.
    Heuristics target list items / short paragraphs that contain code-like tokens.
    Returns list of dicts: {code, reward, expires, source_line}
    """
    soup = BeautifulSoup(html, "html.parser")

    CODE_RE = re.compile(r"\b[A-Z0-9]{6,16}\b")
    EXPIRED_HINTS = re.compile(r"\b(expired|inactive|ended)\b", re.I)

    blocks = []
    for tag in soup.find_all(["li", "p", "div"]):
        txt = tag.get_text(" ", strip=True)
        if txt and len(txt) <= 300:
            blocks.append(txt)

    items: List[Dict] = []
    for line in blocks:
        if EXPIRED_HINTS.search(line or ""):
            continue

        codes = [m.group(0) for m in CODE_RE.finditer(line)]
        if not codes:
            continue

        reward = None
        m = re.search(r"\b[A-Z0-9]{6,16}\b\s*[-–—:]\s*(.+)$", line)
        if m:
            reward = m.group(1).strip()

        exp = None
        m2 = re.search(
            r"(?:valid|expires?|until)\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})",
            line,
            re.I,
        )
        if m2:
            exp = m2.group(1).strip()

        for c in codes:
            items.append(
                {
                    "code": c,
                    "reward": reward,
                    "expires": exp,
                    "source_line": line,
                }
            )

    # De-dupe by code
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
                print(f"[429] Rate limited. Sleeping {retry_after}s…")
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
    parts.append("**Disney Speedstorm — New Code Found!**")
    parts.append(f"`{item['code']}`")
    if item.get("reward"):
        parts.append(f"**Reward:** {item['reward']}")
    if item.get("expires"):
        parts.append(f"**Expires:** {item['expires']}")
    parts.append(f"<{PAGE_URL}>")
    return "\n".join(parts)


def format_health_message(total_seen: int) -> str:
    return (
        "✅ **Speedstorm scraper health check**\n"
        "No new codes today.\n"
        f"Seen codes tracked: **{total_seen}**\n"
        f"Source: <{PAGE_URL}>"
    )


def should_health_ping(state: Dict, now_sp: datetime) -> bool:
    """
    Fire health ping if last ping is ≥ 7 days ago AND there are no new codes.
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

    role_id_env = (os.getenv("ROLE_ID_SPEEDSTORM") or "").strip()
    role_mention_template = f"<@&{role_id_env}>" if role_id_env else None

    html = fetch_html(PAGE_URL)
    items = extract_codes(html)

    state = load_state()
    seen_codes = set(state.get("seen_codes", []))

    new_items = [it for it in items if it["code"] not in seen_codes]

    # ---- Ping-once-per-run control ----
    # We'll only include the role mention on the **first successfully posted** new-code message.
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
