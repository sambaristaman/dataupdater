import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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

# --- HTTP session (reuse connection) ---
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "game8-discord-updater/1.1 (+github-actions)"})


# --- Discord webhook helpers ---

def discord_webhook_post_or_edit(webhook_url: str, message_id: str, content: str) -> Tuple[str, str]:
    """
    Post or edit a message via webhook.
    Returns: (message_id, action) where action is "edited" or "created".
    """
    if DRY_RUN:
        action = "edited" if message_id and not FORCE_NEW else "created"
        return (message_id or "DRY_RUN_MESSAGE_ID", action)

    headers = {"Content-Type": "application/json"}

    if message_id and not FORCE_NEW:
        edit_url = f"{webhook_url}/messages/{message_id}"
        r = SESSION.patch(edit_url, headers=headers, json={"content": content})
        if r.status_code == 200:
            return message_id, "edited"

    post_url = f"{webhook_url}?wait=true"
    r = SESSION.post(post_url, headers=headers, json={"content": content})
    r.raise_for_status()
    data = r.json()
    return data["id"], "created"


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
    """Find containers that contain anchors/headings matching the given titles."""
    roots = []
    tlow = [t.lower() for t in titles]
    # Prefer exact titled headings
    for h in soup.find_all(["h2", "h3"]):
        txt = _clean(h.get_text(" ", strip=True)).lower()
        if txt in tlow:
            roots.append(h)
    if roots:
        return roots
    # Fallback: any anchor with the text
    for a in soup.find_all("a"):
        txt = _clean(a.get_text(" ", strip=True)).lower()
        if txt in tlow:
            roots.append(a)
    return roots

def _find_nearby_link_for_event(head: Tag, base_url: str) -> Optional[str]:
    """After an event h3, find a reasonable link (prefer 'Guide' or same name) within a short window."""
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
            # Prefer something that looks like a guide or references the event name
            if "guide" in label or name.split("—")[0].strip() in label:
                return href
    return None

# Dates like:
#   Event Start September 12, 2025
#   Event End   September 29, 2025
#   or compact 9/12 - 9/29
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
        # Skip junk text quickly
        if _is_junk_text(t):
            continue
        # Compact M/D - M/D
        m = _DATE_RANGE.search(t)
        if m:
            compact = f"{m.group(1)} - {m.group(2)}"
            break
        # Start/End lines
        for part in t.split(" / "):  # handle "Event Start ... / Event End ..."
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
    """
    Genshin page: restrict to 'List of Current Events' and 'List of Upcoming Events' sections,
    gather h3 event blocks, attach a near 'Event Guide' link (filtered to Genshin),
    and clean dates. This version correctly *skips* meta 'Version ...' headings
    but keeps scanning the section for real event h3s.
    """
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
        # Walk forward until we hit the *next* section title (or a major h2 that clearly ends the area)
        for sib in root.next_siblings:
            if not isinstance(sib, Tag):
                continue

            # Hard stop: next section header
            if sib.name in ("h2", "h3") and is_section_title(sib) and sib is not root:
                break

            # Collect event headings within the section
            if sib.name == "h3":
                txt = _clean(sib.get_text(" ", strip=True))
                low = txt.lower()

                # skip meta headings, but DO NOT stop the section
                if "events calendar" in low or "new archives" in low or "upcoming archives" in low:
                    continue
                if "version" in low and "event" in low:
                    # meta like "Version 6.0 ... Current Events" — just skip
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

                if len(bullets) >= 14:  # incl. the header
                    return bullets

            # Some pages wrap h3s inside divs; scan nested h3s too
            for h in sib.find_all("h3"):
                txt = _clean(h.get_text(" ", strip=True))
                low = txt.lower()
                if "events calendar" in low or "new archives" in low or "upcoming archives" in low:
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
    """Target sections like Current/Ongoing/Upcoming/Featured and collect linked bullets."""
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


def build_discord_message(title: str, url: str, last_updated: str, bullets: List[str]) -> str:
    header = f"**{title}**\n<{url}>\n_Last updated on Game8: **{last_updated}**_\n\n"
    body = "\n".join(bullets) if bullets else "_No parseable items found today (site layout may have changed)._"
    content = header + body

    if len(content) > DISCORD_LIMIT:
        extra_note = "\n…and more on the page."
        max_len = DISCORD_LIMIT - len(header) - len(extra_note)
        trimmed = []
        total = 0
        for line in bullets:
            ln = line.strip()
            add = len(ln) + 1
            if total + add > max_len:
                break
            trimmed.append(ln)
            total += add
        content = header + "\n".join(trimmed) + extra_note
    return content


def load_ids() -> Dict[str, str]:
    if MESSAGE_IDS_PATH.exists():
        return json.loads(MESSAGE_IDS_PATH.read_text())
    return {}


def save_ids(ids: Dict[str, str]):
    MESSAGE_IDS_PATH.write_text(json.dumps(ids, indent=2))


def make_summary_embed(results: List[Dict]) -> Dict:
    """Build a nice summary embed of what happened this run."""
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
            value = (
                f"**{r['action'].capitalize()}** message\n"
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
        "footer": {"text": ""},  # no custom footer text
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return embed


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
        content = build_discord_message(nice_title, url, last_updated, bullets)

        old_id = ids.get(key, "")
        new_id, action = discord_webhook_post_or_edit(webhook_url, old_id, content)
        if new_id != old_id:
            ids[key] = new_id
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
