#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Game8 → Discord daily scraper with per-game channels and a summary channel.

Split architecture:
- main.py (this file): orchestration, Discord I/O, state/diffing, and routing
- extractors/genshin_extractor.py: Genshin-specific extraction
- extractors/uma_extractor.py: Umamusume-specific extraction
- extractors/generic_extractor.py: Fallback extraction used by other games
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import requests
from bs4 import BeautifulSoup

# --- Import extractors ---
from extractors.genshin_extractor import extract_genshin_events, extract_genshin_gachas
from extractors.uma_extractor import extract_umamusume_events
from extractors.generic_extractor import extract_events_with_links_generic
from extractors.wuwa_extractor import extract_wuwa_gachas, extract_wuwa_events
from extractors.hsr_extractor import extract_hsr_gachas
from extractors.endfield_extractor import extract_endfield_events, extract_endfield_gachas

# --- Config: pages -> (url, secret_name_for_webhook, pretty_title, secret_name_for_role_id) ---
PAGES = {
    "wuthering-waves": (
        "https://game8.co/games/Wuthering-Waves/archives/453473",
        "WEBHOOK_URL_WUWA",
        "Wuthering Waves — Events & Schedule",
        "ROLE_ID_WUWA",
    ),
    "honkai-star-rail": (
        "https://game8.co/games/Honkai-Star-Rail/archives/408749",
        "WEBHOOK_URL_HSR",
        "Honkai: Star Rail — Events & Schedule",
        "ROLE_ID_HSR",
    ),
    "umamusume": (
        # UPDATED to the page you specified
        "https://game8.co/games/Umamusume-Pretty-Derby/archives/549992",
        "WEBHOOK_URL_UMA",
        "Umamusume: Pretty Derby — Events & Choices",
        "ROLE_ID_UMA",
    ),
    "genshin-impact": (
        "https://game8.co/games/Genshin-Impact/archives/301601",
        "WEBHOOK_URL_GI",
        "Genshin Impact — Archives & Updates",
        "ROLE_ID_GI",
    ),
    "arknights-endfield": (
        "https://game8.co/games/Arknights-Endfield/archives/535443",
        "WEBHOOK_URL_ENDFIELD",
        "Arknights: Endfield — Events",
        "ROLE_ID_ARKNIGHTS_ENDFIELD",
    ),
}

# --- Gacha sources (per game) ---
# key matches PAGES key for channel/webhook routing.
GACHA_PAGES = {
    "genshin-impact": (
        "https://game8.co/games/Genshin-Impact",  # List of Current Event Gachas on main hub
        "genshin",  # type tag for router
    ),
    "wuthering-waves": (
        "https://game8.co/games/Wuthering-Waves/archives/453303",  # Wish/Banner Schedule
        "wuwa",
    ),
    "honkai-star-rail": (
        "https://game8.co/games/Honkai-Star-Rail/archives/408381",  # Warp/Banner Schedule
        "hsr",
    ),
    "arknights-endfield": (
        "https://game8.co/games/Arknights-Endfield/archives/524215",  # Banner Schedule
        "endfield",
    ),
    # Uma deliberately omitted (events already cover banners)
}

MESSAGE_IDS_PATH = Path("message_ids.json")
STATE_PATH = Path("state.json")  # persisted scrape state (for change tracking)
DISCORD_LIMIT = 2000  # characters
SUMMARY_WEBHOOK_ENV = "WEBHOOK_URL_SUMMARY"  # summary channel webhook

# Optional flags for manual runs
ONLY_KEY = os.getenv("ONLY_KEY", "").strip().lower()
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
FORCE_NEW = os.getenv("FORCE_NEW", "false").lower() == "true"

# New: disable Umamusume events entirely (won't appear in updates/summary when disabled)
# Set DISABLE_UMA_EVENTS=true to skip scraping and reporting Uma events.
DISABLE_UMA_EVENTS = os.getenv("DISABLE_UMA_EVENTS", "false").strip().lower() == "true"

# Whether to delete old messages if we decide to re-post new chunked ones
CLEANUP_OLD_MESSAGES = True

# --- HTTP session (reuse connection) ---
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "game8-discord-updater/1.5 (+github-actions)"})


# --- Discord webhook helpers ---

def webhook_edit(webhook_url: str, message_id: str, content: str) -> bool:
    """Try to edit a single existing message. Return True if edited."""
    if DRY_RUN:
        print(f"[DRY_RUN] Would EDIT message {message_id[:6]}... via {webhook_url[:40]}...")
        return True
    r = SESSION.patch(
        f"{webhook_url}/messages/{message_id}",
        headers={"Content-Type": "application/json"},
        json={"content": content},
        timeout=30,
    )
    if r.status_code == 200:
        return True
    print(f"[WARN] Edit failed (status {r.status_code}): {r.text[:200]}")
    return False


def webhook_post(webhook_url: str, content: str) -> str:
    """Post a new message and return its ID."""
    if DRY_RUN:
        print(f"[DRY_RUN] Would POST new message via {webhook_url[:40]}...")
        return "DRY_RUN_MESSAGE_ID"
    r = SESSION.post(
        f"{webhook_url}?wait=true",
        headers={"Content-Type": "application/json"},
        json={"content": content},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def webhook_delete(webhook_url: str, message_id: str) -> None:
    """Delete a message (best-effort; ignore failures)."""
    if DRY_RUN:
        print(f"[DRY_RUN] Would DELETE message {message_id[:6]}...")
        return
    try:
        r = SESSION.delete(f"{webhook_url}/messages/{message_id}", timeout=10)
        if r.status_code not in (200, 204):
            print(f"[WARN] Delete {message_id[:6]} returned status {r.status_code}")
    except Exception as e:
        print(f"[WARN] Delete failed for {message_id[:6]}: {e}")


def discord_webhook_post_embed(webhook_url: str, embed: Dict, content: Optional[str] = None):
    """Send an embed to a webhook (summary channel)."""
    if DRY_RUN:
        print("[DRY_RUN] Would send summary embed." + (f" Content: {content}" if content else ""))
        return
    payload: Dict = {"embeds": [embed]}
    if content:
        payload["content"] = content
    r = SESSION.post(f"{webhook_url}?wait=true", headers={"Content-Type": "application/json"}, json=payload, timeout=30)
    r.raise_for_status()


# --- Scraping helpers ---

def fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def extract_last_updated(soup: BeautifulSoup) -> str:
    # Try precise form first
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Last updated on:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)", text)
    if m:
        return m.group(1)
    # Fallback: line after label (matches also "Last updated on Game8:")
    m2 = re.search(r"Last updated on:\s*([^|]+?)(?=\s{2,}|$)", text)
    return m2.group(1).strip() if m2 else "unknown"


# --- Message building & chunking ---

def build_header(title: str, url: str, last_updated: str) -> str:
    return f"**{title}**\n<{url}>\n_Last updated on Game8: **{last_updated}**_\n"


def chunk_lines_to_messages(header: str, lines: List[str], limit: int = DISCORD_LIMIT) -> List[str]:
    """
    Split header + bullet lines into multiple Discord messages, each <= limit chars.
    First message starts with header, later messages start with '(continued)' line.
    """
    messages: List[str] = []
    current = header.strip() + "\n\n"
    cont_prefix = "_(continued)_\n\n"

    def push():
        nonlocal current
        messages.append(current.rstrip())
        current = cont_prefix

    for ln in lines:
        ln = ln.rstrip()
        add = (ln + "\n")
        if len(current) + len(add) > limit:
            if current.strip():
                push()
        # If a single line is too long, hard-wrap it
        if len(add) > limit - len(current):
            start = 0
            max_chunk = max(100, limit - len(current) - 1)
            while start < len(add):
                chunk = add[start:start + max_chunk]
                if len(current) + len(chunk) > limit:
                    push()
                current += chunk
                start += max_chunk
        else:
            current += add

    if current.strip():
        messages.append(current.rstrip())

    return messages


def build_messages(title: str, url: str, last_updated: str, bullets: List[str]) -> List[str]:
    header = build_header(title, url, last_updated)
    body_lines = bullets if bullets else ["_No parseable items found today (site layout may have changed)._"]
    return chunk_lines_to_messages(header, body_lines, DISCORD_LIMIT)


# --- Persistence helpers (IDs + state) ---

def load_ids() -> Dict[str, Union[str, List[str]]]:
    if MESSAGE_IDS_PATH.exists():
        try:
            return json.loads(MESSAGE_IDS_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_ids(ids: Dict[str, Union[str, List[str]]]):
    if DRY_RUN:
        print("[DRY_RUN] Would write message_ids.json")
        return
    MESSAGE_IDS_PATH.write_text(json.dumps(ids, indent=2))


def load_state() -> Dict[str, Dict]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: Dict[str, Dict]):
    if DRY_RUN:
        print("[DRY_RUN] Would write state.json")
        return
    STATE_PATH.write_text(json.dumps(state, indent=2))


# --- Diff helpers ---

_BULLET_RE = re.compile(
    r"^\s*•\s*(?:\[(?P<label>[^\]]+)\]\((?P<link>[^)]+)\)|(?P<label2>[^—]+?))\s*(?:—\s*(?P<info>.+))?\s*$"
)

def parse_bullet(line: str):
    """
    Return (label, link, info). Works for:
      • [Event](url) — dates/info
      • [Event](url)
      • Event — dates/info
      • Event
    """
    m = _BULLET_RE.match(line.strip())
    if not m:
        return (line.strip().lstrip("• ").strip(), None, None)
    label = (m.group("label") or m.group("label2") or "").strip()
    link = (m.group("link") or None)
    info = (m.group("info") or None)
    return (label, link, info)


def normalize_bullets(bullets: List[str]) -> List[Dict[str, Optional[str]]]:
    out = []
    for b in bullets:
        if b.strip().startswith("__"):  # skip section headers
            continue
        label, link, info = parse_bullet(b)
        if not label:
            continue
        out.append({"label": label, "link": link, "info": info})
    return out


def diff_items(old: List[Dict], new: List[Dict]) -> Dict:
    """
    Compute added/removed/modified:
    - key by label (case-insensitive). If link/info changed, count as modified.
    """
    old_by_label = {i["label"].lower(): i for i in old}
    new_by_label = {i["label"].lower(): i for i in new}

    added, removed, modified = [], [], []

    for lbl, n in new_by_label.items():
        if lbl not in old_by_label:
            added.append(n)
        else:
            o = old_by_label[lbl]
            if (o.get("link") != n.get("link")) or (o.get("info") != n.get("info")):
                modified.append({"before": o, "after": n})

    for lbl, o in old_by_label.items():
        if lbl not in new_by_label:
            removed.append(o)

    return {"added": added, "removed": removed, "modified": modified}


def format_delta(delta: Dict, last_updated_changed: bool) -> str:
    a, r, m = len(delta["added"]), len(delta["removed"]), len(delta["modified"])
    parts = []
    if a or r or m or last_updated_changed:
        if a or r or m:
            parts.append(f"Δ Items: +{a} / −{r} / ~{m}")
        if last_updated_changed:
            parts.append("Game8 timestamp changed")
        notable = []
        for it in delta["added"][:2]:
            notable.append(f"+ {it['label']}")
        for it in delta["removed"][:2]:
            notable.append(f"− {it['label']}")
        for it in delta["modified"][:2]:
            notable.append(f"~ {it['after']['label']}")
        if notable:
            parts.append(" · ".join(notable))
        return " | ".join(parts)
    return "No detected changes"


# --- Summary embed (augmented with delta line) ---

def make_summary_embed(results: List[Dict]) -> Dict:
    total = len(results)
    created = sum(1 for r in results if r.get("action") == "created")
    edited = sum(1 for r in results if r.get("action") == "edited")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    ok = sum(1 for r in results if r["status"] == "ok")

    color = 0x2ECC71 if ok else 0xE67E22

    fields = []
    for r in results:
        name = r["title"]
        if r["status"] == "skipped":
            value = f"⚠️ Skipped (missing secret `{r['secret']}`)"
        else:
            msginfo = f"Messages: **{r.get('messages', 1)}**"
            delta_line = r.get("delta_summary", "No detected changes")
            value = (
                f"**{r['action'].capitalize()}** {msginfo}\n"
                f"Items: **{r['items']}**\n"
                f"Last updated: `{r['last_updated']}`\n"
                f"{delta_line}\n"
                f"[Source]({r['url']})"
            )
        fields.append({"name": name, "value": value, "inline": True})

    embed = {
        "title": "The Mimicky, The Librarian Updates",
        "description": f"✅ OK: **{ok}** · 🆕 Created: **{created}** · ✏️ Edited: **{edited}** · ⏭️ Skipped: **{skipped}** · Total: **{total}**",
        "color": color,
        "fields": fields[:25],
        "footer": {"text": ""},  # only timestamp in Discord UI
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return embed


# --- Router ---

def extract_events_with_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """
    Router: use game-specific logic when needed, else generic.
    Order matters: check Genshin and Umamusume first; others fall back.
    """
    if "/Genshin-Impact/" in base_url:
        gs = extract_genshin_events(soup, base_url)
        if len(gs) >= 3:
            return gs
    if "/Umamusume-Pretty-Derby/" in base_url:
        uma = extract_umamusume_events(soup, base_url)
        if len(uma) >= 1:
            return uma
    if "/Arknights-Endfield/" in base_url:
        endfield = extract_endfield_events(soup, base_url)
        if len(endfield) >= 1:
            return endfield
    if "/Wuthering-Waves/" in base_url:
        wuwa = extract_wuwa_events(soup, base_url)
        if len(wuwa) >= 1:
            return wuwa
    return extract_events_with_links_generic(soup, base_url)


def extract_gacha_for(key: str, soup, url: str) -> list[str]:
    """Route to the correct gacha extractor for a game key."""
    if key == "genshin-impact":
        return extract_genshin_gachas(soup, url)
    if key == "wuthering-waves":
        return extract_wuwa_gachas(soup, url)
    if key == "honkai-star-rail":
        return extract_hsr_gachas(soup, url)
    if key == "arknights-endfield":
        return extract_endfield_gachas(soup, url)
    return []


def run_flow(*, key: str, url: str, secret_name: str, nice_title: str, role_secret: str, ids: Dict, state: Dict, extractor, section_tag: str) -> Dict:
    """Generic runner for either events or gacha. section_tag is used to namespace message IDs and state keys."""
    webhook_url = os.environ.get(secret_name, "").strip()
    role_id = os.environ.get(role_secret, "").strip() if role_secret else ""
    role_mention = f"<@&{role_id}>" if role_id else ""

    if not webhook_url:
        print(f"Missing webhook for {key} (env {secret_name}); skipping {section_tag}.")
        return {
            "key": f"{key}::{section_tag}",
            "title": f"{nice_title} — {section_tag.capitalize()}",
            "url": url,
            "secret": secret_name,
            "status": "skipped",
            "action": "none",
            "items": 0,
            "last_updated": "n/a",
            "delta_summary": "n/a",
            "has_changes": False,
            "role_mention": role_mention,
        }

    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    last_updated = extract_last_updated(soup)
    bullets = extractor(soup, url)

    normalized_now = normalize_bullets(bullets)
    state_key = f"{key}::{section_tag}"
    prev_state = state.get(state_key, {"last_updated": None, "items": []})
    prev_items = prev_state.get("items", [])
    prev_last = prev_state.get("last_updated")

    delta = diff_items(prev_items, normalized_now)
    last_updated_changed = (str(prev_last) != str(last_updated))
    delta_summary = format_delta(delta, last_updated_changed)
    has_changes = bool(delta["added"] or delta["removed"] or delta["modified"] or last_updated_changed)

    messages = build_messages(f"{nice_title} — {section_tag.capitalize()}", url, last_updated, bullets)

    ids_key = f"{key}::{section_tag}"
    prev_ids_raw = ids.get(ids_key, [])
    prev_ids = [prev_ids_raw] if isinstance(prev_ids_raw, str) else list(prev_ids_raw)

    new_ids = []
    action = "edited"
    if len(messages) == 1 and prev_ids and not FORCE_NEW:
        success = webhook_edit(webhook_url, prev_ids[0], messages[0])
        if success:
            new_ids = [prev_ids[0]]
            action = "edited"
            # Clean up extra messages if we went from multiple to 1
            if CLEANUP_OLD_MESSAGES and len(prev_ids) > 1:
                for mid in prev_ids[1:]:
                    webhook_delete(webhook_url, mid)
        else:
            new_ids = [webhook_post(webhook_url, messages[0])]
            action = "created"
            # Clean up all old messages since we created a new one
            if CLEANUP_OLD_MESSAGES:
                for mid in prev_ids:
                    webhook_delete(webhook_url, mid)
    else:
        for content in messages:
            new_ids.append(webhook_post(webhook_url, content))
        action = "created"
        if CLEANUP_OLD_MESSAGES:
            for mid in prev_ids:
                if mid not in new_ids:
                    webhook_delete(webhook_url, mid)

    store_value = new_ids[0] if len(new_ids) == 1 else new_ids
    if store_value != ids.get(ids_key):
        ids[ids_key] = store_value

    items_count = len(bullets)
    if items_count and bullets and bullets[0].startswith("__"):
        items_count -= 1

    result = {
        "key": ids_key,
        "title": f"{nice_title} — {section_tag.capitalize()}",
        "url": url,
        "secret": secret_name,
        "status": "ok",
        "action": action,
        "messages": len(new_ids),
        "items": max(0, items_count),
        "last_updated": last_updated,
        "delta_summary": delta_summary,
        "has_changes": has_changes,
        "role_mention": role_mention,
        "_normalized_now": normalized_now,
    }
    return result

# --- Main flow ---

def main():
    ids = load_ids()
    state = load_state()
    state_changed = False
    results: List[Dict] = []

    # Filter by ONLY_KEY if provided
    items = [(k, v) for k, v in PAGES.items() if not ONLY_KEY or k.lower() == ONLY_KEY]

    # Apply DISABLE_UMA_EVENTS: remove 'umamusume' events entirely so it doesn't appear in updates
    if DISABLE_UMA_EVENTS:
        # If the user explicitly asked ONLY_KEY=umamusume, inform and return early (no results added).
        if ONLY_KEY == "umamusume":
            print("Umamusume events scraping is disabled via DISABLE_UMA_EVENTS; skipping 'umamusume'.")
            # Still allow gacha for other keys if ONLY_KEY strictly equals 'umamusume'—there are none, so exit.
            # No summary entry will be created.
            return
        # Otherwise, drop 'umamusume' from the items list.
        items = [(k, v) for (k, v) in items if k != "umamusume"]

    if not items:
        print(f"No matching keys for ONLY_KEY='{ONLY_KEY}'. Valid keys:", ", ".join(PAGES.keys()))
        return

    # EVENTS
    for key, (url, secret_name, nice_title, role_secret) in items:
        res = run_flow(
            key=key, url=url, secret_name=secret_name, nice_title=nice_title, role_secret=role_secret,
            ids=ids, state=state, extractor=lambda soup, u: extract_events_with_links(soup, u), section_tag="events"
        )
        results.append(res)

    # GACHAS
    gacha_items = [(k, v) for k, v in GACHA_PAGES.items() if not ONLY_KEY or k.lower() == ONLY_KEY]
    for key, (url, type_tag) in gacha_items:
        if key not in PAGES:
            continue
        _, secret_name, nice_title, role_secret = PAGES[key]
        res = run_flow(
            key=key, url=url, secret_name=secret_name, nice_title=nice_title, role_secret=role_secret,
            ids=ids, state=state, extractor=lambda soup, u, _k=key: extract_gacha_for(_k, soup, u), section_tag="gacha"
        )
        results.append(res)

    # Persist IDs/state
    save_ids(ids) if not DRY_RUN else None
    for r in results:
        if r.get("status") != "ok":
            continue
        state[r["key"]] = {
            "last_updated": r["last_updated"],
            "items": r["_normalized_now"],
        }
        r.pop("_normalized_now", None)
        state_changed = True
    if state_changed and not DRY_RUN:
        save_state(state)

    # Summary
    summary_url = os.environ.get(SUMMARY_WEBHOOK_ENV, "").strip()
    if summary_url:
        embed = make_summary_embed(results)
        mentions = sorted({r["role_mention"] for r in results if r.get("has_changes") and r.get("role_mention")})
        mention_content = " ".join(mentions) if mentions else None
        discord_webhook_post_embed(summary_url, embed, mention_content)
    else:
        print(f"No summary webhook found in env {SUMMARY_WEBHOOK_ENV}; skipping summary.")

if __name__ == "__main__":
    main()
