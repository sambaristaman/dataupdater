#!/usr/bin/env python3
import os
import re
import json
import time
from datetime import datetime
from typing import List, Dict, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://shadowverse.gg/"
TRACK_FILE = "shadowverse_news_sent.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# When direct fetch fails (403/503, etc.), we use a READ-ONLY mirror that returns
# a readable Markdown-like rendition of the page content.
# Example: https://r.jina.ai/http://shadowverse.gg/
MIRROR_PREFIX = "https://r.jina.ai/http://"

# ---------------- Utilities ----------------

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

def _direct_get(session: requests.Session, url: str) -> requests.Response:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp

def _mirror_get(session: requests.Session, url: str) -> str:
    """
    Fetch a plain-text/markdown rendition via the mirror service.
    Returns text (NOT HTML).
    """
    # Mirror expects http://… (not https://) in its path
    # (it works with https too, but http is more consistent here).
    if url.startswith("https://"):
        target = url.replace("https://", "http://", 1)
    else:
        target = url
    mirror_url = MIRROR_PREFIX + target.split("://", 1)[1]
    r = session.get(mirror_url, timeout=30)
    r.raise_for_status()
    return r.text

def fetch_html_or_text(session: requests.Session, url: str) -> Tuple[str, str]:
    """
    Returns a tuple (kind, content):
      kind = "html" -> content is HTML
             "text" -> content is plain text/markdown (mirror)
    """
    try:
        r = _direct_get(session, url)
        return "html", r.text
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (403, 429, 503):
            # Fallback to mirror
            text = _mirror_get(session, url)
            return "text", text
        raise
    except Exception:
        # Network / TLS / other issues -> try mirror
        text = _mirror_get(session, url)
        return "text", text

def is_article_url(url: str) -> bool:
    """
    Heuristic for article permalinks coming from the homepage "News" section.
    Avoid top-level sections/utility pages and the /news/ feed.
    """
    if not url.startswith(BASE_URL):
        return False
    path = url[len(BASE_URL):].strip("/")
    if not path:
        return False
    non_articles = {
        "cards","decks","collection","builder","tier-list","events",
        "articles","classes","guides","meta","sets","tournaments",
        "about","contact","privacy","terms","login","news"
    }
    first = path.split("/")[0]
    if first in non_articles:
        return False
    return bool(re.match(r"^[a-z0-9-]+(?:/[a-z0-9-]+)?/?$", path))

# ---------------- Homepage parsing ----------------

def find_news_links_from_home_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []

    news_h = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(strip=True).lower() == "news":
            news_h = h
            break

    if news_h:
        for el in news_h.next_elements:
            if isinstance(el, Tag):
                if el.name in ("h2",) and el is not news_h and el.get_text(strip=True):
                    break  # next major section
                if el.name == "a" and el.has_attr("href"):
                    href = el["href"].split("?")[0].split("#")[0]
                    if is_article_url(href):
                        links.append(href)
    else:
        # Fallback: scan whole page conservatively
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            if is_article_url(href):
                links.append(href)

    # de-dupe
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def find_news_links_from_home_text(txt: str) -> List[str]:
    """
    Mirror returns markdown-like text.
    We first try to isolate the "## News" section, then extract links from that block.
    As a fallback, we gather all shadowverse.gg links and filter with is_article_url().
    """
    block = None
    # Normalize line endings
    content = txt.replace("\r\n", "\n")
    # Try to find a "## News" section
    m = re.search(r"(?im)^##\s*News\s*$", content)
    if m:
        start = m.end()
        # until next H2 (## ...)
        n = re.search(r"(?im)^\s*##\s+\S", content[start:])
        block = content[start:start + n.start()] if n else content[start:]
    else:
        block = content

    # Extract markdown-style links: [text](url)
    candidates = re.findall(r"\((https?://shadowverse\.gg/[^\s)]+)\)", block)
    # Clean URLs, strip anchors & queries
    cleaned = [u.split("?")[0].split("#")[0] for u in candidates]
    links = [u for u in cleaned if is_article_url(u)]

    # de-dupe
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# ---------------- Article extraction ----------------

DATE_PATTERN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
)

def extract_article_from_html(html: str, url: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find(["h1", "h2"])
    title = title_tag.get_text(strip=True) if title_tag else url

    text_for_date = soup.get_text(" ", strip=True)
    dm = DATE_PATTERN.search(text_for_date)
    date_str = dm.group(0) if dm else None

    author = None
    am = re.search(r"\bBy\s+([A-Za-z0-9_.\- ]{2,})\b", text_for_date)
    if am:
        author = am.group(1).strip()

    # Prefer common article containers; fallback to all <p>
    body_container = None
    for selector in ["article", "div.entry-content", "div.post-content", "main"]:
        body_container = soup.select_one(selector)
        if body_container:
            break

    paras: List[str] = []
    if body_container:
        for tag in body_container.find_all(["p", "li", "h2", "h3"]):
            t = tag.get_text(" ", strip=True)
            if t:
                paras.append(t)
    else:
        for p in soup.find_all("p"):
            t = p.get_text(" ", strip=True)
            if t:
                p
