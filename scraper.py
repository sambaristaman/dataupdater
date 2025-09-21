import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

# --- Config: pages -> (url, secret_name_for_webhook, pretty_title) ---
PAGES = {
    "wuthering-waves": (
        "https://game8.co/games/Wuthering-Waves/archives/453473",
        "WEBHOOK_URL_WUWA",
        "Wuthering Waves — Events & Schedule",
    ),
    "honkai-star-rail": (
        "https://game8.co/games/Honkai-Star-Rail/archives/408749",
        "WEBHOOK_URL_HSR",
        "Honkai: Star Rail — Events & Schedule",
    ),
    "umamusume": (
        "https://game8.co/games/Umamusume-Pretty-Derby/archives/539612",
        "WEBHOOK_URL_UMA",
        "Umamusume: Pretty Derby — Events & Choices",
    ),
    "genshin-impact": (
        "https://game8.co/games/Genshin-Impact/archives/301601",
        "WEBHOOK_URL_GI",
        "Genshin Impact — Archives & Updates",
    ),
}

MESSAGE_IDS_PATH = Path("message_ids.json")
DISCORD_LIMIT = 2000  # characters
SUMMARY_WEBHOOK_ENV = "WEBHOOK_URL_SUMMARY"  # summary channel webhook

# Optional flags for manual runs
ONLY_KEY = os.getenv("ONLY_KEY", "").strip().lower()
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
FORCE_NEW = os.getenv("FORCE_NEW", "false").lower() == "true"

# Whether to delete old messages if we decide to re-post new chunked ones
CLEANUP_OLD_MESSAGES = True

# --- HTTP session (reuse connection) ---
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "game8-discord-updater/1.2 (+github-actions)"})


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
    )
    return r.status_code == 200


def webhook_post(webhook_url: str, content: str) -> str:
    """Post a new message and return its ID."""
    if DRY_RUN:
        print(f"[DRY_RUN] Would POST new message via {webhook_url[:40]}...")
        return "DRY_RUN_MESSAGE_ID"
    r = SESSION.post(
        f"{webhook_url}?wait=true",
        headers={"Content-Type": "application/json"},
        json={"content": content},
    )
    r.raise_for_status()
    return r.json()["id"]


def webhook_delete(webhook_url: str, message_id: str) -> None:
    """Delete a message (best-effort; ignore failures)."""
    if DRY_RUN:
        print(f"[DRY_RUN] Would DELETE message {message_id[:6]}...")
        return
    try:
        SESSION.delete(f"{webhook_url}/messages/{message_id}", timeout=10)
    except Exception:
        pass


def discord_webhook_post_embed(webhook_url: str, embed: Dict, content: Optional[str] = None):
    """Send an embed to a webhook (summary channel)."""
    if DRY_RUN:
        print("[DRY_RUN] Would send summary embed.")
        return
    payload: Dict = {"embeds": [embed]}
    if content:
        payload["content"] = content
    r = SESSION.post(f"{webhook_url}?wait=true", headers={"Content-Type": "application/json"}, json=payload)
    r.raise_for_status()


# --- Scraping helpers ---

def fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def extract_last_updated(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Last updated on:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)", text)
    if m:
        return m.group(1)
    m2 = re.search(r"Last updated on:\s*([^|]+?)(?=\s{2,}|$)", text)
    return m2.group(1).strip() if m2 else "unknown"


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    # Prevent accidental Markdown injection
    s = s.replace("**", "").replace("__", "").replace("`", "")
    return s


def _durationish(s: str) -> bool:
    return any(k in s for k in ["Duration", "Event Duration", "期間", "to ", "–", "—", "-"]) or bool(re.search(r"\b\d{4}\b", s))


def _anchor_text(a: Tag) -> str:
    t = a.get_text(" ", strip=True)
    return _clean(t)


def _collect_items_near_head(head: Tag, base_url: str, max_items: int = 12) -> List[str]:
    """Generic: after a heading, collect list rows / table rows with a link + optional duration/info."""
    items: List[str] = []
    seen_links = set()

    for sib in head.find_all_next():
        if sib is head:
            continue
        if sib.name in ["h2", "h3"]:
            break

        candidate_blocks: List[Tag] = []
        if sib.name in ["ul", "ol"]:
            candidate_blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            candidate_blocks.extend(sib.find_all("tr"))
        elif sib.name in ["p", "div"]:
            candidate_blocks.append(sib)

        for block in candidate_blocks:
            a = block.find("a", href=True)
            if not a:
                continue
            label = _anchor_text(a)
            if not label or len(label) < 2:
                continue

            href = a.get("href")
            abs_href = urljoin(base_url, href)
            key = (label.lower(), abs_href)
            if key in seen_links:
                continue
            seen_links.add(key)

            info: Optional[str] = None
            block_text = _clean(block.get_text(" ", strip=True))
            if block_text and block_text.lower() != label.lower():
                bt = block_text
                if bt.lower().startswith(label.lower()):
                    bt = bt[len(label):]
                bt = _clean(bt.strip(":-—– "))
                if _durationish(bt):
                    info = bt

            if not info:
                small = block.find(["small", "span", "em"])
                if small:
                    small_text = _clean(small.get_text(" ", strip=True))
                    if _durationish(small_text):
                        info = small_text

            if not info:
                nxt = block.find_next_sibling(["p", "div"])
                if nxt:
                    nt = _clean(nxt.get_text(" ", strip=True))
                    if _durationish(nt) and len(nt) < 140:
                        info = nt

            line = f"• [{label}]({abs_href})" + (f" — {info}" if info else "")
            items.append(line)

            if len(items) >= max_items:
                return items

    return items


# --- Genshin-specific helpers (tight filters) ---

_SKIP_TEXT_PATTERNS = [
    "create your free account",
    "save articles to your watchlist",
    "save your favorite games",
    "receive instant notifications",
    "convenient features in the comments",
    "site interface",
    "game tools",
]
def _is_junk_text(s: str) -> bool:
    s_low = s.lower()
    return any(p in s_low for p in _SKIP_TEXT_PATTERNS)

def _is_good_genshin_url(u: str) -> bool:
    u = u.lower()
    if "genshin-impact" not in u:
        return False
    if any(x in u for x in ["/account", "/login", "/register", "/tools", "site-interface"]):
        return False
    return True

def _find_section_roots(soup: BeautifulSoup, titles: List[str]) -> List[Tag]:
    roots = []
    tlow = [t.lower() for t in titles]
    for h in soup.find_all(["h2", "h3"]):
        txt = _clean(h.get_text(" ", strip=True)).lower()
        if txt in tlow:
            roots.append(h)
    if roots:
        return roots
    for a in soup.find_all("a"):
        txt = _clean(a.get_text(" ", strip=True)).lower()
        if txt in tlow:
            roots.append(a)
    return roots

def _find_nearby_link_for_event(head: Tag, base_url: str) -> Optional[str]:
    name = _clean(head.get_text(" ", strip=True)).lower()
    for sib in head.find_all_next(limit=40):
        if sib is head:
            continue
        if sib.name == "h3":  # next event block starts
            break
        for a in sib.find_all("a", href=True):
            label = _anchor_text(a).lower()
            href = urljoin(base_url, a["href"])
            if not _is_good_genshin_url(href):
                continue
            if "guide" in label or name.split("—")[0].strip() in label:
                return href
    return None

_DATE_WORD = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s*\d{4}"
_DATE_RANGE = re.compile(r"(\d{1,2}/\d{1,2})\s*[-–—]\s*(\d{1,2}/\d{1,2})", re.I)
_DATE_LINE = re.compile(r"(event start|event end)[:\s]*(" + _DATE_WORD + ")", re.I)

def _collect_dates_after(head: Tag) -> Optional[str]:
    start = end = None
    compact = None
    for sib in head.find_all_next(limit=20):
        if sib is head:
            continue
        if sib.name == "h3":
            break
        t = _clean(sib.get_text(" ", strip=True))
        if not t:
            continue
        if _is_junk_text(t):
            continue
        m = _DATE_RANGE.search(t)
        if m:
            compact = f"{m.group(1)} - {m.group(2)}"
            break
        for part in t.split(" / "):
            m2 = _DATE_LINE.search(part)
            if m2:
                kind = m2.group(1).lower()
                date_str = m2.group(2)
                if "start" in kind and not start:
                    start = date_str
                elif "end" in kind and not end:
                    end = date_str
        if start and end:
            break

    if compact:
        return compact
    if start or end:
        return f"Start {start}" if start and not end else (f"End {end}" if end and not start else f"{start} → {end}")
    return None

def extract_genshin_events(soup: BeautifulSoup, base_url: str) -> List[str]:
    SECTION_TITLES = ["List of Current Events", "List of Upcoming Events"]
    section_roots = _find_section_roots(soup, SECTION_TITLES)
    if not section_roots:
        return []  # fall back to generic

    bullets: List[str] = ["__List of Current/Upcoming Events__"]
    seen = set()

    def is_section_title(tag: Tag) -> bool:
        if not hasattr(tag, "get_text"):
            return False
        txt = _clean(tag.get_text(" ", strip=True)).lower()
        return txt in {t.lower() for t in SECTION_TITLES}

    for root in section_roots:
        for sib in root.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name in ("h2", "h3") and is_section_title(sib) and sib is not root:
                break
            if sib.name == "h3":
                txt = _clean(sib.get_text(" ", strip=True))
                low = txt.lower()
                if any(k in low for k in ["events calendar", "new archives", "upcoming archives"]):
                    continue
                if "version" in low and "event" in low:
                    continue
                if _is_junk_text(low) or len(txt.split()) < 2:
                    continue
                link = _find_nearby_link_for_event(sib, base_url)
                if link and not _is_good_genshin_url(link):
                    link = None
                dates = _collect_dates_after(sib)
                key = (low, link or "")
                if key in seen:
                    continue
                seen.add(key)
                if link and dates:
                    bullets.append(f"• [{txt}]({link}) — {dates}")
                elif link:
                    bullets.append(f"• [{txt}]({link})")
                elif dates:
                    bullets.append(f"• {txt} — {dates}")
                else:
                    bullets.append(f"• {txt}")
                if len(bullets) >= 14:
                    return bullets
            for h in sib.find_all("h3"):
                txt = _clean(h.get_text(" ", strip=True))
                low = txt.lower()
                if any(k in low for k in ["events calendar", "new archives", "upcoming archives"]):
                    continue
                if "version" in low and "event" in low:
                    continue
                if _is_junk_text(low) or len(txt.split()) < 2:
                    continue
                link = _find_nearby_link_for_event(h, base_url)
                if link and not _is_good_genshin_url(link):
                    link = None
                dates = _collect_dates_after(h)
                key = (low, link or "")
                if key in seen:
                    continue
                seen.add(key)
                if link and dates:
                    bullets.append(f"• [{txt}]({link}) — {dates}")
                elif link:
                    bullets.append(f"• [{txt}]({link})")
                elif dates:
                    bullets.append(f"• {txt} — {dates}")
                else:
                    bullets.append(f"• {txt}")
                if len(bullets) >= 14:
                    return bullets

    return bullets


# --- Generic extractor (other games) ---

def extract_events_with_links_generic(soup: BeautifulSoup, base_url: str) -> List[str]:
    headings = soup.find_all(["h2", "h3", "h4"])
    key_heads = [h for h in headings if any(
        t in h.get_text(strip=True).lower()
        for t in ["current events", "ongoing events", "events calendar", "upcoming", "featured events", "new archives", "upcoming archives"]
    )]

    bullets: List[str] = []
    if key_heads:
        for head in key_heads:
            title = _clean(head.get_text(" ", strip=True))
            bullets.append(f"__{title}__")
            bullets.extend(_collect_items_near_head(head, base_url, max_items=10))
    else:
        seen = set()
        for a in soup.find_all("a", href=True):
            label = _anchor_text(a)
            if not label or len(label) < 3:
                continue
            href = urljoin(base_url, a["href"])
            key = (label.lower(), href)
            if key in seen:
                continue
            if href.endswith("#") or href.startswith("mailto:"):
                continue
            info = None
            parent = a.find_parent(["li", "p", "div", "tr"])
            if parent:
                pt = _clean(parent.get_text(" ", strip=True))
                if _durationish(pt) and len(pt) < 160:
                    info = pt
            line = f"• [{label}]({href})" + (f" — {info}" if info else "")
            bullets.append(line)
            seen.add(key)
            if len(bullets) >= 12:
                break

    final: List[str] = []
    seen_line = set()
    for b in bullets:
        if b not in seen_line:
            final.append(b)
            seen_line.add(b)
    return final[:40]


def extract_events_with_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Router: use Genshin-specific logic first for that page, else generic."""
    if "/Genshin-Impact/" in base_url:
        gs = extract_genshin_events(soup, base_url)
        if len(gs) >= 3:
            return gs
    return extract_events_with_links_generic(soup, base_url)


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
            # conservative split at ~1800 chars
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


# --- Persistence helpers ---

def load_ids() -> Dict[str, Union[str, List[str]]]:
    if MESSAGE_IDS_PATH.exists():
        try:
            return json.loads(MESSAGE_IDS_PATH.read_text())
        except Exception:
            return {}
    return {}

def save_ids(ids: Dict[str, Union[str, List[str]]]):
    MESSAGE_IDS_PATH.write_text(json.dumps(ids, indent=2))


# --- Summary embed ---

def make_summary_embed(results: List[Dict]) -> Dict:
    total = len(results)
    created = sum(1 for r in results if r["action"] == "created")
    edited = sum(1 for r in results if r["action"] == "edited")
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
            value = (
                f"**{r['action'].capitalize()}** {msginfo}\n"
                f"Items: **{r['items']}**\n"
                f"Last updated: `{r['last_updated']}`\n"
                f"[Source]({r['url']})"
            )
        fields.append({"name": name, "value": value, "inline": True})

    embed = {
        "title": "Game8 → Discord: Daily Update",
        "description": f"✅ OK: **{ok}** · 🆕 Created: **{created}** · ✏️ Edited: **{edited}** · ⏭️ Skipped: **{skipped}** · Total: **{total}**",
        "color": color,
        "fields": fields[:25],
        "footer": {"text": ""},  # only timestamp in Discord UI
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return embed


# --- Main flow ---

def main():
    ids = load_ids()
    changed = False
    results: List[Dict] = []

    items = [(k, v) for k, v in PAGES.items() if not ONLY_KEY or k.lower() == ONLY_KEY]
    if not items:
        print(f"No matching keys for ONLY_KEY='{ONLY_KEY}'. Valid keys:", ", ".join(PAGES.keys()))
        return

    for key, (url, secret_name, nice_title) in items:
        webhook_url = os.environ.get(secret_name, "").strip()
        if not webhook_url:
            print(f"Missing webhook for {key} (env {secret_name}); skipping.")
            results.append({
                "key": key,
                "title": nice_title,
                "url": url,
                "secret": secret_name,
                "status": "skipped",
                "action": "none",
                "items": 0,
                "last_updated": "n/a",
            })
            continue

        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        last_updated = extract_last_updated(soup)
        bullets = extract_events_with_links(soup, url)

        # Build possibly multiple messages (chunks)
        messages = build_messages(nice_title, url, last_updated, bullets)

        prev_ids_raw = ids.get(key, [])
        if isinstance(prev_ids_raw, str):
            prev_ids = [prev_ids_raw]
        else:
            prev_ids = list(prev_ids_raw)

        # Decide behavior:
        # - If single chunk AND not FORCE_NEW -> try to edit the first existing message.
        # - If multiple chunks OR FORCE_NEW -> post fresh messages, then optionally delete old ones.
        new_ids: List[str] = []
        action = "edited"

        if len(messages) == 1 and prev_ids and not FORCE_NEW:
            success = webhook_edit(webhook_url, prev_ids[0], messages[0])
            if success:
                new_ids = [prev_ids[0]]
                action = "edited"
            else:
                # if edit failed, post fresh
                new_ids = [webhook_post(webhook_url, messages[0])]
                action = "created"
        else:
            # Over page limit or we're forcing new: post new messages
            for content in messages:
                new_ids.append(webhook_post(webhook_url, content))
            action = "created"
            # Clean up old messages (best-effort)
            if CLEANUP_OLD_MESSAGES:
                for mid in prev_ids:
                    if mid not in new_ids:
                        webhook_delete(webhook_url, mid)

        # Persist IDs (store list when >1, else single string for readability)
        store_value: Union[str, List[str]] = new_ids[0] if len(new_ids) == 1 else new_ids
        if store_value != ids.get(key):
            ids[key] = store_value
            changed = True

        # compute item count sans our injected section header
        items_count = len(bullets)
        if items_count and bullets[0].startswith("__"):
            items_count -= 1

        results.append({
            "key": key,
            "title": nice_title,
            "url": url,
            "secret": secret_name,
            "status": "ok",
            "action": action,
            "messages": len(new_ids),
            "items": max(0, items_count),
            "last_updated": last_updated,
        })

    if changed and not DRY_RUN:
        save_ids(ids)
        print("message_ids.json updated.")
    else:
        print("No message ID changes." if not DRY_RUN else "Dry run complete (no writes).")

    summary_url = os.environ.get(SUMMARY_WEBHOOK_ENV, "").strip()
    if summary_url:
        embed = make_summary_embed(results)
        discord_webhook_post_embed(summary_url, embed, None)
        print("Summary embed sent.")
    else:
        print(f"No summary webhook found in env {SUMMARY_WEBHOOK_ENV}; skipping summary.")


if __name__ == "__main__":
    main()
