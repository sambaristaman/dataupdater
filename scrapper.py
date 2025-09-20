import json
import os
import re
from pathlib import Path
from typing import Dict, Tuple, List, Optional
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

# --- Discord webhook helpers ---

def discord_webhook_post_or_edit(webhook_url: str, message_id: str, content: str) -> str:
    session = requests.Session()
    headers = {"Content-Type": "application/json"}

    if message_id:
        edit_url = f"{webhook_url}/messages/{message_id}"
        r = session.patch(edit_url, headers=headers, json={"content": content})
        if r.status_code == 200:
            return message_id

    post_url = f"{webhook_url}?wait=true"
    r = session.post(post_url, headers=headers, json={"content": content})
    r.raise_for_status()
    data = r.json()
    return data["id"]

# --- Scraping helpers ---

def fetch(url: str) -> str:
    r = requests.get(url, timeout=30)
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
    """Walk the section after a heading and collect lines with [link] + optional duration/info."""
    items: List[str] = []
    seen_links = set()

    # Scan siblings until next h2/h3
    for sib in head.find_all_next():
        if sib is head:
            continue
        if sib.name in ["h2", "h3"]:
            break

        # capture list rows / table rows / paragraphs
        candidate_blocks: List[Tag] = []
        if sib.name in ["ul", "ol"]:
            candidate_blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            candidate_blocks.extend(sib.find_all("tr"))
        elif sib.name in ["p", "div"]:
            candidate_blocks.append(sib)

        for block in candidate_blocks:
            # Find the best anchor (prefer first meaningful <a>)
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

            # Try to harvest a nearby duration/info line
            info: Optional[str] = None
            # 1) text inside the same block (minus the anchor label)
            block_text = _clean(block.get_text(" ", strip=True))
            if block_text and block_text.lower() != label.lower():
                bt = block_text
                if bt.lower().startswith(label.lower()):
                    bt = bt[len(label):]
                bt = _clean(bt.strip(":-—– "))
                if _durationish(bt):
                    info = bt

            # 2) look at a following small tag or sibling
            if not info:
                small = block.find(["small", "span", "em"])
                if small:
                    small_text = _clean(small.get_text(" ", strip=True))
                    if _durationish(small_text):
                        info = small_text

            # 3) fallback: scan the next paragraph
            if not info:
                nxt = block.find_next_sibling(["p", "div"])
                if nxt:
                    nt = _clean(nxt.get_text(" ", strip=True))
                    if _durationish(nt) and len(nt) < 140:
                        info = nt

            if info:
                line = f"• [{label}]({abs_href}) — {info}"
            else:
                line = f"• [{label}]({abs_href})"
            items.append(line)

            if len(items) >= max_items:
                return items

    return items

def extract_events_with_links(soup: BeautifulSoup, base_url: str) -> List[str]:
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
        # Fallback: harvest any anchor + nearby duration anywhere on the page (top 12)
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

    # compact dedupe and trim
    final: List[str] = []
    seen_line = set()
    for b in bullets:
        if b not in seen_line:
            final.append(b)
            seen_line.add(b)
    return final[:40]

def build_discord_message(title: str, url: str, last_updated: str, bullets: List[str]) -> str:
    header = f"**{title}**\n<{url}>\n_Last updated on Game8: **{last_updated}**_\n\n"
    body = "\n".join(bullets) if bullets else "_No parseable items found today (site layout may have changed)._"
    content = header + body

    # Respect Discord 2000 char limit
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

def main():
    ids = load_ids()
    changed = False

    for key, (url, secret_name, nice_title) in PAGES.items():
        webhook_url = os.environ.get(secret_name, "").strip()
        if not webhook_url:
            print(f"Missing webhook for {key} (env {secret_name}); skipping.")
            continue

        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")

        last_updated = extract_last_updated(soup)
        bullets = extract_events_with_links(soup, url)
        content = build_discord_message(nice_title, url, last_updated, bullets)

        old_id = ids.get(key, "")
        new_id = discord_webhook_post_or_edit(webhook_url, old_id, content)
        if new_id != old_id:
            ids[key] = new_id
            changed = True

    if changed:
        save_ids(ids)
        print("message_ids.json updated.")
    else:
        print("No message ID changes.")

if __name__ == "__main__":
    main()
