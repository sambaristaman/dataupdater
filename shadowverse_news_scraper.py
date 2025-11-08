#!/usr/bin/env python3
import os
import re
import json
import time
import random
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
MIRROR_PREFIX = "https://r.jina.ai/http://"

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# ---------------- State ----------------

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

def ensure_state_file_exists(sent: Set[str]) -> None:
    if not os.path.exists(TRACK_FILE):
        save_sent(sent)

# ---------------- HTTP helpers ----------------

def _retry_sleep(attempt: int, base: float = 3.0) -> None:
    wait = base * attempt + random.uniform(0, 1)
    print(f"…retrying in {wait:.1f}s")
    time.sleep(wait)

def direct_get(session: requests.Session, url: str, retries: int = 3, timeout: int = 60) -> requests.Response:
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"[direct] {type(e).__name__} on {url} (attempt {attempt}/{retries})")
            if attempt == retries:
                raise
            _retry_sleep(attempt)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            print(f"[direct] HTTP {code} on {url} (attempt {attempt}/{retries})")
            # retry only 5xx
            if code and 500 <= code < 600 and attempt < retries:
                _retry_sleep(attempt)
                continue
            raise

def mirror_get(session: requests.Session, url: str, retries: int = 2, timeout: int = 60) -> str:
    if url.startswith("https://"):
        target = url.replace("https://", "http://", 1)
    else:
        target = url
    mirror_url = MIRROR_PREFIX + target.split("://", 1)[1]
    for attempt in range(1, retries + 1):
        try:
            r = session.get(mirror_url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"[mirror] {type(e).__name__} (attempt {attempt}/{retries}) on {mirror_url}")
            if attempt == retries:
                raise
            _retry_sleep(attempt, base=2.0)

def fetch_html_or_text(session: requests.Session, url: str) -> Tuple[str, str]:
    try:
        r = direct_get(session, url)
        return "html", r.text
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (403, 429, 503):
            print(f"[fallback] Using mirror for {url} due to HTTP {code}")
            txt = mirror_get(session, url)
            return "text", txt
        raise
    except Exception as e:
        print(f"[fallback] Network issue on {url}: {e}; using mirror")
        txt = mirror_get(session, url)
        return "text", txt

# ---------------- Link discovery ----------------

def is_article_url(url: str) -> bool:
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
                    break
                if el.name == "a" and el.has_attr("href"):
                    href = el["href"].split("?")[0].split("#")[0]
                    if is_article_url(href):
                        links.append(href)
    else:
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            if is_article_url(href):
                links.append(href)
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def find_news_links_from_home_text(txt: str) -> List[str]:
    content = txt.replace("\r\n", "\n")
    m = re.search(r"(?im)^##\s*News\s*$", content)
    if m:
        start = m.end()
        n = re.search(r"(?im)^\s*##\s+\S", content[start:])
        block = content[start:start + n.start()] if n else content[start:]
    else:
        block = content
    candidates = re.findall(r"\((https?://shadowverse\.gg/[^\s)]+)\)", block)
    cleaned = [u.split("?")[0].split("#")[0] for u in candidates]
    links = [u for u in cleaned if is_article_url(u)]
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# ---------------- Article parsing ----------------

DATE_PATTERN = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}")

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
                paras.append(t)
    full_text = "\n".join(paras).strip()
    summary = (full_text[:3200].rstrip() + "\n...\n(continued on site)") if len(full_text) > 3500 else full_text
    iso_ts = None
    if date_str:
        try:
            iso_ts = datetime.strptime(date_str, "%B %d, %Y").isoformat()
        except Exception:
            pass
    return {"url": url, "title": title, "date_str": date_str, "timestamp": iso_ts, "author": author, "summary": summary}

def extract_article_from_text(txt: str, url: str) -> Dict:
    content = txt.replace("\r\n", "\n")
    m = re.search(r"(?m)^#\s+(.+)$", content)
    title = m.group(1).strip() if m else url
    dm = DATE_PATTERN.search(content)
    date_str = dm.group(0) if dm else None
    iso_ts = None
    if date_str:
        try:
            iso_ts = datetime.strptime(date_str, "%B %d, %Y").isoformat()
        except Exception:
            pass
    am = re.search(r"\bBy\s+([A-Za-z0-9_.\- ]{2,})\b", content)
    author = am.group(1).strip() if am else None
    body = content[m.end():].strip() if m else content.strip()
    body = re.sub(r"(?s)\n##\s*Related.*$", "", body).strip()
    summary = (body[:3200].rstrip() + "\n...\n(continued on site)") if len(body) > 3500 else body
    return {"url": url, "title": title, "date_str": date_str, "timestamp": iso_ts, "author": author, "summary": summary}

# ---------------- Discord ----------------

def send_to_discord(webhook_url: str, article: Dict) -> None:
    embed = {
        "title": (article.get("title") or article["url"])[:256],
        "url": article["url"],
        "description": (article.get("summary") or "(Open the link to read.)")[:4096],
        "footer": {"text": "Shadowverse.gg"},
    }
    if article.get("author"):
        embed["author"] = {"name": article["author"][:256]}
    if article.get("timestamp"):
        embed["timestamp"] = article["timestamp"]
    payload = {"embeds": [embed]}
    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook error {resp.status_code}: {resp.text[:300]}")

# ---------------- Main ----------------

def main():
    webhook = os.environ.get("WEBHOOK_URL_NEWS")
    if not webhook:
        raise SystemExit("Missing env var WEBHOOK_URL_NEWS")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})

    sent = load_sent()

    # 1) homepage
    try:
        kind, home_content = fetch_html_or_text(session, BASE_URL)
    except Exception as e:
        print(f"Failed to fetch homepage: {e}")
        # ensure state file and exit gracefully
        ensure_state_file_exists(sent)
        return

    # 2) discover links
    links = find_news_links_from_home_html(home_content) if kind == "html" \
        else find_news_links_from_home_text(home_content)
    print(f"Discovered {len(links)} candidate links on homepage.")

    # 3) filter already sent (skip opening subpages)
    new_links = [u for u in links if u not in sent]
    print(f"{len(new_links)} new links after filtering sent.")

    # ensure state file exists even if nothing to do
    ensure_state_file_exists(sent)
    if not new_links:
        print("No new items.")
        return

    # 4) process each new link
    for url in new_links:
        try:
            try:
                kind2, content2 = fetch_html_or_text(session, url)
                article = extract_article_from_html(content2, url) if kind2 == "html" \
                    else extract_article_from_text(content2, url)
                if not article.get("summary"):
                    article["summary"] = "(Open the link to read — blocked from scraping right now.)"
                if not article.get("title"):
                    article["title"] = url
            except Exception as suberr:
                print(f"Subpage blocked/unavailable for {url}: {suberr}")
                # Minimal message (title from slug)
                slug = url.rstrip("/").split("/")[-1]
                title_guess = slug.replace("-", " ").title() if slug else url
                article = {
                    "url": url,
                    "title": title_guess,
                    "date_str": None,
                    "timestamp": None,
                    "author": None,
                    "summary": "(Open the link to read — blocked from scraping right now.)",
                }

            if DRY_RUN:
                print(f"[DRY_RUN] Would send: {article['title']} -> {article['url']}")
            else:
                send_to_discord(webhook, article)

            sent.add(url)
            save_sent(sent)
            time.sleep(2.0)  # be polite
        except Exception as e:
            print(f"Failed for {url}: {e}")
            time.sleep(2.0)

if __name__ == "__main__":
    main()
