#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Game codes -> Discord webhook notifier (Pocket Tactics, with PocketGamer as fallback).
- Scrapes active codes for the game selected by GAME (nte, aniimo, endfield).
- Posts ONE webhook message per newly discovered active code.
- Optional role ping, but **only once per run** (first successfully posted new-code message).
- Warns on the summary webhook when the primary source fails/recovers.
- Weekly health ping to the summary webhook when there's no new code for >= 7 days.

Env:
  GAME                        -> game key: nte | aniimo | endfield (required)
  WEBHOOK_URL_CODEX           -> Discord webhook URL for code alerts (required)
  WEBHOOK_URL_SUMMARY         -> Discord webhook URL for warnings and health pings (optional)
  ROLE_ID_NTE / ROLE_ID_ANIIMO / ROLE_ID_ARKNIGHTS_ENDFIELD
                              -> Discord role ID to @mention on new codes (optional; ping once/run)
  DRY_RUN=true                -> don't post or write state, just print (optional)
"""

import json
import os
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import re

from bs4 import BeautifulSoup, NavigableString, Tag

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

GAMES: Dict[str, Dict] = {
    "nte": {
        "display": "Neverness to Everness",
        "primary": "https://www.pockettactics.com/neverness-to-everness/codes",
        "fallback": "https://www.pocketgamer.com/neverness-to-everness/codes/",
        "state_path": "nte_codes_state.json",
        "role_env": "ROLE_ID_NTE",
    },
    "aniimo": {
        "display": "Aniimo",
        "primary": "https://www.pockettactics.com/aniimo/codes",
        "fallback": "https://www.pocketgamer.com/aniimo/codes/",
        "state_path": "aniimo_codes_state.json",
        "role_env": "ROLE_ID_ANIIMO",
    },
    "endfield": {
        "display": "Arknights: Endfield",
        "primary": "https://www.pockettactics.com/arknights-endfield/codes",
        "fallback": "https://www.pocketgamer.com/arknights-endfield/codes/",
        "state_path": "arknights_endfield_codes_state.json",
        "role_env": "ROLE_ID_ARKNIGHTS_ENDFIELD",
    },
}

CODE_RE = re.compile(r"^[A-Za-z0-9]{4,24}$")


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _first_child(li: Tag):
    for child in li.children:
        if isinstance(child, NavigableString) and not child.strip():
            continue
        return child
    return None


def parse_codes(html: str) -> List[Dict[str, Optional[str]]]:
    """
    Extract codes from a "bold code - reward" bullet list.
    Stops at the first heading that mentions "expired".
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("article") or soup

    items: List[Dict[str, Optional[str]]] = []
    seen = set()
    for el in root.find_all(["h2", "h3", "h4", "li"]):
        if el.name != "li":
            if "expired" in el.get_text(" ").lower():
                break
            continue
        if el.find_parent(["s", "del"]) or el.find(["s", "del"]):
            continue
        first = _first_child(el)
        if not isinstance(first, Tag) or first.name not in ("strong", "b"):
            continue
        code = _clean_text(first.get_text(" ")).rstrip(":-–—").strip()
        if not CODE_RE.match(code):
            continue
        key = normalize_code(code)
        if key in seen:
            continue
        seen.add(key)
        rest = _clean_text(el.get_text(" "))[len(_clean_text(first.get_text(" "))):]
        reward = rest.lstrip(" :-–—").strip()
        items.append({"code": code, "reward": reward or None})
    return items


def fetch_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def load_state(path: Path) -> Dict:
    path = Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(path: Path, state: Dict):
    Path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


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


def format_new_code_message(display: str, item: Dict, source_url: str, role_mention: Optional[str]) -> str:
    parts = []
    if role_mention:
        parts.append(role_mention)
    parts.append(f"**{display} — New Code Found!**")
    parts.append(f"`{item['code']}`")
    if item.get("reward"):
        parts.append(f"**Rewards:** {item['reward']}")
    parts.append(f"<{source_url}>")
    return "\n".join(parts)


def format_health_message(display: str, total_seen: int, source_url: str) -> str:
    return (
        f"**{display} scraper health check**\n"
        "No new codes today.\n"
        f"Seen codes tracked: **{total_seen}**\n"
        f"Source: <{source_url}>"
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


def _try_source(url: str) -> Tuple[List[Dict], Optional[str]]:
    """Return (items, failure_reason). A page with zero parsed codes counts as a failure."""
    try:
        items = parse_codes(fetch_html(url))
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if not items:
        return [], "no codes found on page"
    return items, None


def run(game: str) -> int:
    cfg = GAMES[game]
    display = cfg["display"]
    is_dry = (os.getenv("DRY_RUN", "false").lower() == "true")
    now_sp = datetime.now(ZoneInfo("America/Sao_Paulo"))

    webhook_codes = (os.getenv("WEBHOOK_URL_CODEX") or "").strip()
    webhook_summary = (os.getenv("WEBHOOK_URL_SUMMARY") or "").strip()
    role_id = (os.getenv(cfg["role_env"]) or "").strip()
    role_mention = f"<@&{role_id}>" if role_id else None

    state_path = Path(cfg["state_path"])
    state = load_state(state_path)
    seen_codes = {normalize_code(c) for c in state.get("seen_codes", [])}
    first_run = not seen_codes

    items, primary_err = _try_source(cfg["primary"])
    status, source_url, fallback_err = "primary", cfg["primary"], None
    if primary_err:
        if first_run:
            status = "none"
        else:
            items, fallback_err = _try_source(cfg["fallback"])
            if fallback_err:
                status = "none"
            else:
                status, source_url = "fallback", cfg["fallback"]

    previous = state.get("last_source", "primary")
    if status != previous:
        if status == "primary":
            warning = f"**{display} codes:** primary source is back"
        elif status == "fallback":
            warning = f"**{display} codes:** primary source failed ({primary_err}); using fallback {cfg['fallback']}"
        else:
            reasons = f"primary: {primary_err}"
            if fallback_err:
                reasons += f"; fallback: {fallback_err}"
            warning = f"**{display} codes:** no source available ({reasons})"
        print(f"[WARN] {warning}")
        if webhook_summary:
            post_webhook(webhook_summary, warning)

    state["last_source"] = status
    if status == "none":
        if not is_dry:
            save_state(state_path, state)
        return 1

    print(f"[Info] Found {len(items)} active codes on page ({status}).")
    new_items = [it for it in items if normalize_code(it["code"]) not in seen_codes]

    ping_available = bool(role_mention)
    posted = []
    for it in new_items:
        content = format_new_code_message(display, it, source_url, role_mention if ping_available else None)
        mid = post_webhook(webhook_codes, content)
        if mid:
            print(f"[OK] Announced new code {it['code']} (message id={mid})")
            posted.append(it)
            ping_available = False
        else:
            print(f"[WARN] Failed to announce code {it['code']}")

    seen_codes.update(normalize_code(it["code"]) for it in posted)
    state["seen_codes"] = sorted(seen_codes)

    if not new_items and webhook_summary and should_health_ping(state, now_sp):
        mid = post_webhook(webhook_summary, format_health_message(display, len(seen_codes), source_url))
        if mid:
            print(f"[OK] Health ping sent (message id={mid})")
            state["last_health_ping_iso"] = now_sp.isoformat()
        else:
            print("[WARN] Failed to send health ping.")

    if not new_items:
        print("[Info] No new codes.")

    if not is_dry:
        save_state(state_path, state)
    return 0


def main():
    game = (os.getenv("GAME") or "").strip().lower()
    if game not in GAMES:
        raise SystemExit(f"Missing or invalid GAME env var (expected one of: {', '.join(GAMES)}).")
    if not (os.getenv("WEBHOOK_URL_CODEX") or "").strip():
        raise SystemExit("Missing WEBHOOK_URL_CODEX env var.")
    sys.exit(run(game))


if __name__ == "__main__":
    main()
