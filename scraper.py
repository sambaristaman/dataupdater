#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Banners & events → Discord daily scraper with per-game channels and a summary channel.

Architecture:
- scraper.py (this file): orchestration, Discord I/O, state/diffing and summary
- sources/: per-game sources that return structured Items (see sources/common.py)
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

import requests

from sources.aniimo import aniimo_events
from sources.common import Source, SourceResult, build_bullets
from sources.endfield import endfield_events, endfield_gacha
from sources.gametora import uma_events, uma_gacha
from sources.hoyo import genshin_events, genshin_gacha, hsr_events, hsr_gacha
from sources.nte import nte_events, nte_gacha
from sources.wuwa import wuwa_events, wuwa_gacha


# --- Config: one entry per game (key -> webhook/role secrets, title and sources) ---

@dataclass
class GameConfig:
    secret_name: str
    title: str
    role_secret: str
    events: Optional[Source]
    gacha: Optional[Source]


GAMES: Dict[str, GameConfig] = {
    "wuthering-waves": GameConfig(
        "WEBHOOK_URL_WUWA", "Wuthering Waves — Events & Banners", "ROLE_ID_WUWA",
        wuwa_events, wuwa_gacha,
    ),
    "honkai-star-rail": GameConfig(
        "WEBHOOK_URL_HSR", "Honkai: Star Rail — Events & Banners", "ROLE_ID_HSR",
        hsr_events, hsr_gacha,
    ),
    "umamusume": GameConfig(
        "WEBHOOK_URL_UMA", "Umamusume: Pretty Derby — Events & Banners", "ROLE_ID_UMA",
        uma_events, uma_gacha,
    ),
    "genshin-impact": GameConfig(
        "WEBHOOK_URL_GI", "Genshin Impact — Events & Banners", "ROLE_ID_GI",
        genshin_events, genshin_gacha,
    ),
    "arknights-endfield": GameConfig(
        "WEBHOOK_URL_ENDFIELD", "Arknights: Endfield — Events & Banners", "ROLE_ID_ARKNIGHTS_ENDFIELD",
        endfield_events, endfield_gacha,
    ),
    "nte": GameConfig(
        "WEBHOOK_URL_NTE", "Neverness to Everness — Events & Banners", "ROLE_ID_NTE",
        nte_events, nte_gacha,
    ),
    "aniimo": GameConfig(
        "WEBHOOK_URL_ANIIMO", "Aniimo — Events", "ROLE_ID_ANIIMO",
        aniimo_events, None,
    ),
}

NO_ITEMS_LINE = "_No current or upcoming items._"

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

# --- HTTP session for Discord webhooks ---
SESSION = requests.Session()


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


# --- Message building & chunking ---

def build_header(title: str, url: str, source_name: str) -> str:
    return f"**{title}**\n<{url}>\n_Source: **{source_name}**_\n"


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


def build_messages(title: str, url: str, source_name: str, bullets: List[str]) -> List[str]:
    header = build_header(title, url, source_name)
    body_lines = bullets if bullets else [NO_ITEMS_LINE]
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
            parts.append("Source changed")
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
    errors = sum(1 for r in results if r["status"] == "error")
    ok = sum(1 for r in results if r["status"] == "ok")

    color = 0xE74C3C if errors else (0x2ECC71 if ok else 0xE67E22)

    fields = []
    for r in results:
        name = r["title"]
        if r["status"] == "skipped":
            value = f"⚠️ Skipped (missing secret `{r['secret']}`)"
        elif r["status"] == "error":
            reason = str(r.get("diag_reason") or "unknown")[:200]
            value = f"❌ Error: `{reason}`"
            if r.get("url"):
                value += f"\n[Source]({r['url']})"
        else:
            msginfo = f"Messages: **{r.get('messages', 1)}**"
            delta_line = r.get("delta_summary", "No detected changes")
            value = (
                f"**{r['action'].capitalize()}** {msginfo}\n"
                f"Items: **{r['items']}**\n"
                f"Source: `{r['last_updated']}`\n"
                f"{delta_line}\n"
                f"[Source]({r['url']})"
            )
        fields.append({"name": name, "value": value, "inline": True})

    embed = {
        "title": "The Mimicky, The Librarian Updates",
        "description": (
            f"✅ OK: **{ok}** · 🆕 Created: **{created}** · ✏️ Edited: **{edited}** · "
            f"⏭️ Skipped: **{skipped}** · ❌ Errors: **{errors}** · Total: **{total}**"
        ),
        "color": color,
        "fields": fields[:25],
        "footer": {"text": ""},  # only timestamp in Discord UI
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return embed


# --- Flow ---

def _error_result(base: Dict, reason: str, url: str = "") -> Dict:
    print(f"[ERROR] {base['key']}: {reason}")
    return {
        **base,
        "url": url,
        "status": "error",
        "action": "none",
        "items": 0,
        "last_updated": "n/a",
        "delta_summary": "n/a",
        "has_changes": False,
        "diag_reason": reason,
    }


def run_flow(*, key: str, cfg: GameConfig, source: Source, section_tag: str, ids: Dict, state: Dict, now: datetime) -> Dict:
    """Generic runner for either events or gacha. section_tag namespaces message IDs and state keys."""
    webhook_url = os.environ.get(cfg.secret_name, "").strip()
    role_id = os.environ.get(cfg.role_secret, "").strip() if cfg.role_secret else ""
    role_mention = f"<@&{role_id}>" if role_id else ""
    ids_key = f"{key}::{section_tag}"
    title = f"{cfg.title} — {section_tag.capitalize()}"
    base = {"key": ids_key, "title": title, "secret": cfg.secret_name, "role_mention": role_mention}

    if not webhook_url:
        print(f"Missing webhook for {key} (env {cfg.secret_name}); skipping {section_tag}.")
        return {
            **base,
            "url": "",
            "status": "skipped",
            "action": "none",
            "items": 0,
            "last_updated": "n/a",
            "delta_summary": "n/a",
            "has_changes": False,
        }

    try:
        result: SourceResult = source(now)
    except Exception as e:
        return _error_result(base, f"{type(e).__name__}: {e}")
    if not result.items:
        return _error_result(base, "no_items", result.source_url)

    url = result.source_url
    source_name = result.source_name
    bullets = build_bullets(result.items, now)
    print(f"{ids_key}: {source_name} → {len(result.items)} items, "
          f"{sum(1 for b in bullets if b.startswith('•'))} shown")

    normalized_now = normalize_bullets(bullets)
    prev_state = state.get(ids_key, {"last_updated": None, "items": []})
    prev_items = prev_state.get("items", [])
    prev_last = prev_state.get("last_updated")

    delta = diff_items(prev_items, normalized_now)
    last_updated_changed = (str(prev_last) != str(source_name))
    delta_summary = format_delta(delta, last_updated_changed)
    has_changes = bool(delta["added"] or delta["removed"] or delta["modified"] or last_updated_changed)

    messages = build_messages(title, url, source_name, bullets)

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

    return {
        **base,
        "url": url,
        "status": "ok",
        "action": action,
        "messages": len(new_ids),
        "items": sum(1 for b in bullets if b.strip().startswith("•")),
        "last_updated": source_name,
        "delta_summary": delta_summary,
        "has_changes": has_changes,
        "_normalized_now": normalized_now,
    }


def _safe_run_flow(**kwargs) -> Dict:
    """run_flow, but an unexpected failure (e.g. Discord error) only fails this flow."""
    try:
        return run_flow(**kwargs)
    except Exception as e:
        cfg = kwargs["cfg"]
        key = f"{kwargs['key']}::{kwargs['section_tag']}"
        base = {
            "key": key,
            "title": f"{cfg.title} — {kwargs['section_tag'].capitalize()}",
            "secret": cfg.secret_name,
            "role_mention": "",
        }
        return _error_result(base, f"{type(e).__name__}: {e}")


# --- Main flow ---

def main():
    ids = load_ids()
    state = load_state()
    state_changed = False
    results: List[Dict] = []
    now = datetime.now(timezone.utc)

    # Filter by ONLY_KEY if provided
    games = [(k, cfg) for k, cfg in GAMES.items() if not ONLY_KEY or k.lower() == ONLY_KEY]
    if not games:
        print(f"No matching keys for ONLY_KEY='{ONLY_KEY}'. Valid keys:", ", ".join(GAMES.keys()))
        return

    # DISABLE_UMA_EVENTS only drops the Umamusume events flow; its gacha flow still runs.
    if DISABLE_UMA_EVENTS:
        print("Umamusume events scraping is disabled via DISABLE_UMA_EVENTS; skipping 'umamusume::events'.")

    for key, cfg in games:
        flows = [("events", cfg.events), ("gacha", cfg.gacha)]
        for section_tag, source in flows:
            if source is None:
                continue
            if section_tag == "events" and key == "umamusume" and DISABLE_UMA_EVENTS:
                continue
            results.append(_safe_run_flow(
                key=key, cfg=cfg, source=source, section_tag=section_tag,
                ids=ids, state=state, now=now,
            ))

    # Persist IDs/state (only successful flows touch state)
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
    summary_failed = False
    summary_url = os.environ.get(SUMMARY_WEBHOOK_ENV, "").strip()
    if summary_url:
        embed = make_summary_embed(results)
        mentions = sorted({r["role_mention"] for r in results if r.get("has_changes") and r.get("role_mention")})
        mention_content = " ".join(mentions) if mentions else None
        try:
            discord_webhook_post_embed(summary_url, embed, mention_content)
        except Exception as e:
            print(f"[ERROR] summary post failed: {type(e).__name__}: {e}")
            summary_failed = True
    else:
        print(f"No summary webhook found in env {SUMMARY_WEBHOOK_ENV}; skipping summary.")

    errors = [r["key"] for r in results if r.get("status") == "error"]
    if errors:
        print(f"[ERROR] {len(errors)} flow(s) failed: {', '.join(errors)}")
    if errors or summary_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
