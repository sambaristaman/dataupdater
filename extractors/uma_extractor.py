# -*- coding: utf-8 -*-
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

# --- Local helpers (self-contained; no imports from main) ---

def _clean(s: str) -> str:
    import re as _re
    s = _re.sub(r"\s+", " ", s).strip()
    return s.replace("**", "").replace("__", "").replace("`", "")

def _anchor_text(a: Tag) -> str:
    return _clean(a.get_text(" ", strip=True))

def _durationish(s: str) -> bool:
    return any(k in s for k in ["Duration", "Event Duration", "期間", "to ", "–", "—", "-"]) or bool(re.search(r"\b\d{4}\b", s))

def _bad_href(u: str) -> bool:
    if not u:
        return True
    ul = u.lower().strip()
    if ul.startswith("javascript:") or ul.startswith("mailto:") or ul.endswith("#"):
        return True
    if any(p in ul for p in ["/login", "/register", "/signup", "/account"]):
        return True
    return False

def _is_good_umamusume_url(u: str) -> bool:
    if _bad_href(u):
        return False
    ul = u.lower()
    if "umamusume-pretty-derby" not in ul:
        return False
    if any(x in ul for x in ["/login", "/register", "/signup", "/account", "javascript:void(0)"]):
        return False
    return True

_BULLET_RE = re.compile(
    r"^\s*•\s*(?:\[(?P<label>[^\]]+)\]\((?P<link>[^)]+)\)|(?P<label2>[^—]+?))\s*(?:—\s*(?P<info>.+))?\s*$"
)
def _parse_bullet(line: str) -> Tuple[str, Optional[str], Optional[str]]:
    m = _BULLET_RE.match(line.strip())
    if not m:
        return (line.strip().lstrip("• ").strip(), None, None)
    label = (m.group("label") or m.group("label2") or "").strip()
    link = (m.group("link") or None)
    info = (m.group("info") or None)
    return (label, link, info)

def _find_article_root(soup: BeautifulSoup) -> Tag:
    """
    Game8 pages generally wrap the main content in an article/body container.
    Constrain to this area to avoid global nav/footers.
    """
    candidates = [
        soup.find(id=re.compile(r"(article|content).*(body|main)", re.I)),
        soup.find(class_=re.compile(r"(article|content).*(body|main)", re.I)),
        soup.find("article"),
        soup.find("main"),
    ]
    for c in candidates:
        if isinstance(c, Tag):
            return c
    return soup  # fallback

def _collect_items_near_head(head: Tag, base_url: str, max_items: int = 10) -> List[str]:
    items: List[str] = []
    seen = set()

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
            href = a.get("href", "")
            if not _is_good_umamusume_url(href):
                continue

            label = _anchor_text(a)
            if not label or len(label) < 2:
                continue

            abs_href = urljoin(base_url, href)
            key = (label.lower(), abs_href)
            if key in seen:
                continue
            seen.add(key)

            info = None
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

# --- Public API ---

def extract_umamusume_events(soup: BeautifulSoup, base_url: str) -> List[str]:
    """
    Focus on sections present on the Umamusume Events/Choices page and
    ignore site-chrome links like 'Sign Up' / 'Log In'.
    """
    root = _find_article_root(soup)
    SECTION_HINTS = [
        "ongoing events",
        "current events",
        "event list",
        "event choices",
        "story events",
        "campaigns",
        "races",
        "training events",
        "latest events",
        "featured events",
    ]

    heads: List[Tag] = []
    for h in root.find_all(["h2", "h3"]):
        txt = _clean(h.get_text(" ", strip=True)).lower()
        if any(hint in txt for hint in SECTION_HINTS):
            heads.append(h)

    bullets: List[str] = []
    if heads:
        for head in heads[:4]:
            title = _clean(head.get_text(" ", strip=True))
            bullets.append(f"__{title}__")
            for line in _collect_items_near_head(head, base_url, max_items=10):
                # Tighten to Umamusume-specific links only
                label, link, info = _parse_bullet(line)
                if link and not _is_good_umamusume_url(link):
                    continue
                bullets.append(line)
    else:
        # Conservative fallback: scan anchors in article root only and whitelist the game path
        seen = set()
        for a in root.find_all("a", href=True):
            href = a["href"]
            if not _is_good_umamusume_url(href):
                continue
            label = _anchor_text(a)
            if not label or len(label) < 3:
                continue
            abs_href = urljoin(base_url, href)
            key = (label.lower(), abs_href)
            if key in seen:
                continue
            seen.add(key)

            # Try to capture short duration/info from the nearest text
            info = None
            parent = a.find_parent(["li", "p", "div", "tr"])
            if parent:
                pt = _clean(parent.get_text(" ", strip=True))
                if _durationish(pt) and len(pt) < 160:
                    info = pt
            bullets.append(f"• [{label}]({abs_href})" + (f" — {info}" if info else ""))
            if len(bullets) >= 12:
                break

    # De-dup and trim
    final: List[str] = []
    seen_line = set()
    for b in bullets:
        if b not in seen_line:
            final.append(b)
            seen_line.add(b)
    return final[:40]
