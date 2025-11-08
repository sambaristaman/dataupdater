#!/usr/bin/env python3
import os
import re
import json
import time
import hashlib
from datetime import datetime
from typing import List, Dict, Set, Tuple
import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://shadowverse.gg/"
TRACK_FILE = "shadowverse_news_sent.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ------------- Utilities -------------

def load_sent() -> Set[str]:
    if not os.path.exists(TRACK_FILE):
        return set()
    try:
        with open(TRACK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("sent_urls", []))
    except Exception:
        return set()

def save_sent(sent: Set[str]) -> None:
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        json.dump({"sent_urls": sorted(sent)}, f, indent=2, ensure_ascii=False)

def get(session: requests.Session, url: str) -> requests.Response:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp

def is_article_url(url: str) -> bool:
    """
    Heuristic for article permalinks on the homepage.
    We avoid known non-article top-level sections and utility pages.
    """
    if not url.startswith(BASE_URL):
        return False
    path = url[len(BASE_URL):].strip("/")
    if not path:
        return False  # homepage
    # Filter out obvious non-article endpoints:
    non_articles = {
        "cards","decks","collection","builder","tier-list","events",
        "articles","classes","guides","meta","sets","tournaments",
        "about","contact","privacy","terms","login","news"  # do not rely on /news/ feed
    }
    first = path.split("/")[0]
    if first in non_articles:
        return False
    # Likely a post if it’s a single slug (or slug-like path)
    # e.g., "best-early-decks-for-every-class-skybound-dragons"
    return bool(re.match(r"^[a-z0-9-]+(?:/[a-z0-9-]+)?/?$", path))

def find_news_links_from_home(soup: BeautifulSoup) -> List[str]:
    """
    Prefer links that appear under the 'News' section heading.
    Fall back to deduped article-like links across the page if the section isn’t found.
    """
    links: List[str] = []

    # Primary: Find the "News" heading and collect article anchors that follow it
    news_h2 = None
    for h in soup.find_all(["h2", "h3"]):
        txt = h.get_text(strip=True).lower()
        if txt == "news":
            news_h2 = h
            break

    if news_h2:
        # Traverse forward siblings until the next major section (another h2 with simple text or footer)
        for el in news_h2.next_elements:
            if isinstance(el, Tag):
                # Stop if we reach another high-level header which looks like a section divider
                if el.name in ("h2",) and el is not news_h2 and el.get_text(strip=True):
                    break
                if el.name == "a" and el.has_attr("href"):
                    href = el["href"].split("?")[0].split("#")[0]
                    if is_article_url(href):
                        links.append(href)

    # Fallback: if nothing found (site layout changed), scan entire page
    if not links:
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            if is_article_url(href):
                links.append(href)

    # Dedupe, preserve order
    seen = set()
    ordered = []
    for u in links:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered

def extract_article(session: requests.Session, url: str) -> Dict:
    """
    Pull title, date (if found), author (if found) and text content.
    """
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Title
    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(strip=True) if title_tag else url

    # Date (search for a recognizable date in the page)
    # Example dates on the site: "October 30, 2025"
    text_for_date = soup.get_text(" ", strip=True)
    date_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
        text_for_date
    )
    date_str = date_match.group(0) if date_match else None

    # Author (look for a small block near the title, or common patterns)
    author = None
    # Try simple pattern "By XYZ" or the author name near the top
    auth_match = re.search(r"\bBy\s+([A-Za-z0-9_.\- ]{2,})\b", text_for_date)
    if auth_match:
        author = auth_match.group(1).strip()
    else:
        # Try to find an <a> near the title that looks like an author link
        if title_tag:
            candidate = title_tag.find_next(["a", "span"])
            if candidate and candidate.get_text(strip=True):
                # Very loose heuristic; if it looks like a name, use it
                cand_txt = candidate.get_text(strip=True)
                if 2 <= len(cand_txt) <= 40 and "shadowverse.gg" not in cand_txt.lower():
                    author = cand_txt

    # Body: prefer main article container if present; else collect <p> under <article>
    body_container = None
    for selector in [
        "article",                 # WordPress-like
        "div.entry-content",       # Common WP class
        "div.post-content",
        "main"
    ]:
        body_container = soup.select_one(selector)
        if body_container:
            break

    paragraphs: List[str] = []
    if body_container:
        # Collect visible text-ish nodes (p and list items, headings for structure)
        for tag in body_container.find_all(["p", "li", "h2", "h3"]):
            txt = tag.get_text(" ", strip=True)
            if not txt:
                continue
            # Skip common placeholders like ad-block messages
            if "If you see this for too long, please disable AdBlock" in txt:
                continue
            paragraphs.append(txt)
    else:
        # Fallback: grab all <p> on the page
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt:
                paragraphs.append(txt)

    # Join into a single message body but keep Discord limits in mind
    full_text = "\n".join(paragraphs).strip()
    if len(full_text) > 3500:
        summary = full_text[:3200].rstrip() + "\n...\n(continued on site)"
    else:
        summary = full_text

    # ISO timestamp if date parsed
    iso_ts = None
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%B %d, %Y")
            iso_ts = dt.isoformat()
        except Exception:
            pass

    return {
        "url": url,
        "title": title,
        "date_str": date_str,
        "timestamp": iso_ts,
        "author": author,
        "summary": summary
    }

def send_to_discord(webhook_url: str, article: Dict) -> None:
    """
    Use Discord webhook with an embed for richer formatting.
    """
    embed = {
        "title": article["title"][:256],
        "url": article["url"],
        "description": article["summary"][:4096],
        "footer": {"text": "Shadowverse.gg"},
    }
    if article.get("author"):
        embed["author"] = {"name": article["author"][:256]}
    if article.get("timestamp"):
        embed["timestamp"] = article["timestamp"]

    payload = {
        "embeds": [embed]
    }
    # If you prefer a channel ping or label, set "content": "New Shadowverse article!" etc.

    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text[:300]}")

# ------------- Main flow -------------

def main():
    webhook = os.environ.get("WEBHOOK_URL_NEWS")
    if not webhook:
        raise SystemExit("Missing env var WEBHOOK_URL_NEWS")

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    session = requests.Session()
    session.headers.update(headers)

    sent = load_sent()

    # 1) Scrape homepage
    home = get(session, BASE_URL)
    soup = BeautifulSoup(home.text, "html.parser")

    # 2) Collect candidate links from the News section
    links = find_news_links_from_home(soup)

    # 3) Filter out anything we've already sent BEFORE opening pages
    new_links = [u for u in links if u not in sent]

    if not new_links:
        print("No new items.")
        return

    # 4) For each new link, open, parse, and send
    for url in new_links:
        try:
            article = extract_article(session, url)
            # Basic validation: ensure we got a title and a non-empty summary
            if not article["title"] or not article["summary"]:
                # Skip silently if content looks empty/broken
                continue
            send_to_discord(webhook, article)
            sent.add(url)
            save_sent(sent)
            # Be polite to the site and to Discord
            time.sleep(2.0)
        except Exception as e:
            # Log and continue to next item
            print(f"Failed for {url}: {e}")
            time.sleep(2.0)

if __name__ == "__main__":
    main()
