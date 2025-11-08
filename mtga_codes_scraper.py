#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MTG Arena → Discord webhook notifier (Draftsim).
- Scrapes active codes from: https://draftsim.com/mtg-arena-codes/
- Monitors these sections (treated uniformly, but labeled in messages):
  * MTG Arena Booster Pack Codes
  * MTG Arena Cosmetic Codes
  * MTG Arena Experience Codes
  * MTG Arena Card Codes
  * MTG Arena Deck Codes
- Posts ONE webhook message per newly discovered active code (across all sections).
- Optional role ping, but **only once per run** (first successfully posted new-code message).
- Weekly health ping to a separate summary webhook when there's no new code for ≥ 7 days.

Env:
  WEBHOOK_URL_CODEX   -> Discord webhook URL for MTG Arena alerts (required)
  WEBHOOK_URL_SUMMARY -> Discord webhook URL for health pings (optional)
  ROLE_ID_MTGA        -> Discord role ID to @mention on new codes (optional; ping once/run)
  DRY_RUN=true        -> don't post, just print (optional)
"""

import json
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

PAGE_URL = "https://draftsim.com/mtg-arena-codes/"
STATE_PATH = Path("mtga_codes_state.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "mtga-codes/1.0 (+discord-webhook)"})


def fetch_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _normalize_code(code: str) -> str:
    # MTGA codes are typically case-insensitive but shown uppercase on Draftsim.
    return _clean_text(code).upper()


def _is_placeholder(code_text: str) -> bool:
    # Skip placeholder rows like "None currently", "N/A", etc.
    t = code_text.strip().lower()
    return (not t) or ("none" in t) or (t in {"n/a", "na"})


def _iter_section_tables(soup: BeautifulSoup) -> List[Tuple[str, Tag]]:
    """
    Find section <h2> headers for categories and the first following <table>.
    Returns list of (category, table_tag).
    """
    wanted = {
        "mtg arena booster pack codes": "Booster Pack",
        "mtg arena cosmetic codes": "Cosmetic",
        "mtg arena experience codes": "Experience",
        "mtg arena card codes": "Card",
        "mtg arena deck codes": "Deck",
    }
    out: List[Tuple[str, Tag]] = []

    for h in soup.find_all(["h2", "h3"]):
        heading = _clean_text(h.get_text(" "))
        key = heading.lower()
        if key in wanted:
            # find the next table after this header
            nxt = h.find_next(lambda t: isinstance(t, Tag) and t.name in ("table", "div"))
            table = None
            # Some pages wrap tables in a div; walk until a table or a new heading appears
            p = h
            while p:
                p = p.find_next_sibling()
                if not p:
                    break
                if isinstance(p, Tag) and p.name in ("h2", "h3"):
                    break
                if isinstance(p, Tag) and p.name == "table":
                    table = p
                    break
            if table:
                out.append((wanted[key], table))
    return out


def _parse_table(table: Tag) -> List[Dict]:
    """
    Expect columns 'Code', 'Reward', 'Expiration Date' (case-insensitive).
    Gracefully handle unexpected orders / extra columns.
    """
    rows = table.find_all("tr")
    if not rows:
        return []

    # Build header map
    headers = [ _clean_text(th.get_text(" ")) for th in rows[0].find_all(["th", "td"]) ]
    idx_code = idx_reward = idx_exp = None
    for i, h in enumerate(h.lower() for h in headers):
        if idx_code is None and ("code" in h):
            idx_code = i
        if idx_reward is None and ("reward" in h):
            idx_reward = i
        if idx_exp is None and ("expire" in h or "expiration" in h or "expiry" in h or "date" in h):
            idx_exp = i

    items: List[Dict] = []
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        # Safe getters
        def get(idx):
            if idx is None or idx >= len(cells):
                return ""
            return _clean_text(cells[idx].get_text(" "))

        code_raw = get(idx_code) if idx_code is not None else _clean_text(cells[0].get_text(" "))
        if _is_placeholder(code_raw):
            continue

        code = _normalize_code(code_raw)
        reward = get(idx_reward) if idx_reward is not None else ""
        exp = get(idx_exp) if idx_exp is not None else ""

        # Normalize "Unknown" / "N/A"
        if exp.strip().lower() in {"unknown", "n/a", "na", ""}:
            exp = None
        items.append({"code": code, "reward": reward or None, "expires": exp})
    return items


def extract_codes(html: str) -> List[Dict]:
    """
    Extract codes from Draftsim's MTG Arena codes page.
    Returns: list of dicts {code, reward, expires, category, source_line}
    """
    soup = BeautifulSoup(html, "html.parser")
    collected: List[Dict] = []

    for category, table in _iter_section_tables(soup):
        parsed = _parse_table(table)
        for it in parsed:
            it["category"] = category
            it["source_line"] = f"{category} — {it['code']} — {it.get('reward') or ''} — {it.get('expires') or ''}"
            collected.append(it)

    # Fallback: If nothing parsed (structure shift), scan for code-like tokens in short blocks
    if not collected:
        CODE_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9\-_.]{3,30}\b")
        EXPIRED_HINTS = re.compile(r"\b(expired|inactive|ended)\b", re.I)

        blocks = []
        for tag in soup.find_all(["li", "p", "div"]):
            txt = _clean_text(tag.get_text(" "))
            if txt and 3 <= len(txt) <= 300:
                blocks.append(txt)

        seen = set()
        for line in blocks:
            if EXPIRED_HINTS.search(line):
                continue
            # crude category detection from nearby text
            cat = None
            low = line.lower()
            if "booster" in low or "pack" in low:
                cat = "Booster Pack"
            elif "cosmetic" in low or "style" in low or "sleeve" in low:
                cat = "Cosmetic"
            elif "xp" in low or "experience" in low or "mastery" in low:
                cat = "Experience"
            elif "card" in low:
                cat = "Card"
            elif "deck" in low:
                cat = "Deck"
            else:
                cat = "Uncategorized"

            # reward and expiration candidates
            reward = None
            m = re.search(r"\b([A-Za-z ]+):\s*(.+)$", line)
            if m:
                reward = m.group(2).strip()

            exp = None
            m2 = re.search(
                r"(?:valid|expires?|until)\s*[:\-]?\s*([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})",
                line,
                re.I,
            )
            if m2:
                exp = m2.group(1).strip()

            for m3 in CODE_RE.finditer(line):
                code = _normalize_code(m3.group(0))
                if code in seen or _is_placeholder(code):
                    continue
                seen.add(code)
                collected.append(
                    {"code": code, "reward": reward, "expires": exp, "category": cat, "source_line": line}
                )

    # De-dupe by code only (code may occasionally appear in multiple sections)
    deduped: List[Dict] = []
    seen_codes = set()
    for it in collected:
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
    parts.append("**MTG Arena — New Code Found!**")
    parts.append(f"**Category:** {item.get('category', 'Unknown')}")
    parts.append(f"`{item['code']}`")
    if item.get("reward"):
        parts.append(f"**Reward:** {item['reward']}")
    if item.get("expires"):
        parts.append(f"**Expires:** {item['expires']}")
    parts.append(f"<{PAGE_URL}>")
    return "\n".join(parts)


def format_health_message(total_seen: int) -> str:
    return (
        "✅ **MTG Arena scraper health check**\n"
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

    role_id_env = (os.getenv("ROLE_ID_MTGA") or "").strip()
    role_mention_template = f"<@&{role_id_env}>" if role_id_env else None

    html = fetch_html(PAGE_URL)
    items = extract_codes(html)

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
