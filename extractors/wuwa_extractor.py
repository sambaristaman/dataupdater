# wuwa_extractor.py

from typing import List, Optional
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

def _clean(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", s or "").strip()

def _durationish(s: str) -> bool:
    s = s or ""
    # catches "Sep 17, 2025 - Oct 8, 2025", "to", dashes, and years
    return any(k in s for k in ["Duration", "Event Duration", "期間", " to ", "–", "—", "-"]) or bool(re.search(r"\b\d{4}\b", s))

def _find_article_root(soup: BeautifulSoup) -> Tag:
    for cand in [
        soup.find(id=re.compile(r"(article|content).*(body|main)", re.I)),
        soup.find(class_=re.compile(r"(article|content).*(body|main)", re.I)),
        soup.find("article"),
        soup.find("main"),
    ]:
        if isinstance(cand, Tag):
            return cand
    return soup

_CURRENT_HEAD_HINTS = [
    "available convene banners",
    "all active convene banners",
    "current banners",
    "current banner",
    "ongoing banner",
    "ongoing banners",
    "featured",
    "rate-up",
]

_UPCOMING_HEAD_HINTS = [
    "upcoming banners",
    "upcoming convene banners",
    "next banners",
    "future banners",
]

_SECTION_BREAK_HINTS = [
    # keep things that should end the CURRENT scan; upcoming will be parsed separately
    "upcoming banners",
    "permanent banners",
    "all convene (gacha) simulators",
    "all wuwa version banner history",
    "comment",
    "related guides",
]

def _bad_href(u: str) -> bool:
    if not u:
        return True
    ul = u.lower().strip()
    if ul.startswith(("javascript:", "mailto:")) or ul.endswith("#"):
        return True
    if any(p in ul for p in ["/login", "/register", "/signup", "/account", "site-interface"]):
        return True
    return False

def _is_section_break(tag: Tag) -> bool:
    txt = _clean(tag.get_text(" ", strip=True)).lower()
    return any(h in txt for h in _SECTION_BREAK_HINTS)

def _gather_date_near(head: Tag) -> Optional[str]:
    MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
    RANGE = re.compile(rf"{MONTH}\s+\d{{1,2}},\s*\d{{4}}\s*[-–—]\s*{MONTH}\s+\d{{1,2}},\s*\d{{4}}", re.I)
    for sib in head.find_all_next(limit=80):
        if sib is head:
            continue
        if getattr(sib, "name", "") in ("h2", "h3") and _is_section_break(sib):
            break
        text = _clean(getattr(sib, "get_text", lambda *_: "")(" ", strip=True))
        if not text:
            continue
        m = RANGE.search(text)
        if m:
            return m.group(0)
        if _durationish(text) and 8 < len(text) < 80 and re.search(r"\b\d{4}\b", text):
            return text
    return None

def _collect_links_in_section(head: Tag, base_url: str, max_items: int = 12) -> List[str]:
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue
        if getattr(sib, "name", "") in ("h2", "h3") and _is_section_break(sib):
            break

        blocks: List[Tag] = []
        if sib.name in ("ul", "ol"):
            blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            blocks.extend(sib.find_all("tr"))
        elif sib.name in ("div", "p"):
            blocks.append(sib)

        for blk in blocks:
            for a in blk.find_all("a", href=True):
                label = _clean(a.get_text(" ", strip=True))
                if not label or len(label) < 2:
                    continue
                href = a.get("href", "")
                if _bad_href(href):
                    continue
                abs_href = urljoin(base_url, href)
                key = (label.lower(), abs_href)
                if key in seen:
                    continue
                seen.add(key)

                info = None
                bt = _clean(blk.get_text(" ", strip=True))
                if bt and bt.lower() != label.lower() and _durationish(bt):
                    pruned = _clean(bt.replace(label, "")).strip(": -—–")
                    if pruned and pruned != label and len(pruned) < 140:
                        info = pruned

                items.append(f"• [{label}]({abs_href})" + (f" — {info}" if info else ""))
                if len(items) >= max_items:
                    return items

    return items

def _first_head_with_hints(root: Tag, hints: list[str]) -> Optional[Tag]:
    for h in root.find_all(["h2", "h3", "h4"]):
        t = _clean(h.get_text(" ", strip=True)).lower()
        if any(k in t for k in hints):
            return h
    return None

def extract_wuwa_gachas(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Parse the Wuthering Waves banner schedule page for current AND upcoming banners."""
    bullets: List[str] = []
    root = _find_article_root(soup)

    # CURRENT
    current_head = _first_head_with_hints(root, _CURRENT_HEAD_HINTS)
    cur_lines: List[str] = ["__Current Banners / Gacha__"]
    if current_head:
        shared_dates = _gather_date_near(current_head)
        items = _collect_links_in_section(current_head, base_url, max_items=12)

        # Prefer likely banner titles
        NICE = [line for line in items if any(k in line.lower() for k in ["banner", "convene", "pulsation", "waxes", "rhythms"])]
        chosen = NICE or items

        if shared_dates:
            fixed = []
            for line in chosen:
                fixed.append(line if " — " in line else f"{line} — {shared_dates}")
            chosen = fixed
        cur_lines.extend(chosen[:12])
    else:
        cur_lines.append("• _No parseable banners found (layout may have changed)._")
    bullets.extend(cur_lines)

    # UPCOMING
    upcoming_head = _first_head_with_hints(root, _UPCOMING_HEAD_HINTS)
    if upcoming_head:
        up_items = _collect_links_in_section(upcoming_head, base_url, max_items=12)
        if up_items:
            bullets.append("__Upcoming Banners / Gacha__")
            bullets.extend(up_items[:12])

    return bullets


# --- Events extractor for WuWa ---

_WUWA_CURRENT_EVENTS_HINTS = ["ongoing events", "current events", "featured events"]
_WUWA_UPCOMING_EVENTS_HINTS = ["upcoming events", "future events"]
_WUWA_STOP_HINTS = ["permanent events", "related guides", "comment", "all events"]


def _match_event_header(tag: Tag, hints: list[str]) -> bool:
    """Check if a header tag matches any of the hint phrases."""
    t = _clean(tag.get_text(" ", strip=True)).lower()
    return any(h in t for h in hints)


def _collect_events_from_section(head: Tag, base_url: str, stop_on: list[str], max_items: int = 12) -> List[str]:
    """Walk forward from header, collecting event links from tables and lists."""
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue

        # Stop at headers that indicate a different section
        if getattr(sib, "name", "") in ("h2", "h3", "h4"):
            t = _clean(sib.get_text(" ", strip=True)).lower()
            if any(h in t for h in stop_on + _WUWA_STOP_HINTS):
                break

        # Collect from tables
        if sib.name == "table":
            for row in sib.find_all("tr"):
                for a in row.find_all("a", href=True):
                    label = _clean(a.get_text(" ", strip=True))
                    if not label or len(label) < 2:
                        continue
                    href = a.get("href", "")
                    if _bad_href(href):
                        continue
                    abs_href = urljoin(base_url, href)
                    key = (label.lower(), abs_href)
                    if key in seen:
                        continue
                    seen.add(key)

                    # Extract date info from row
                    info = None
                    row_text = _clean(row.get_text(" ", strip=True))
                    if row_text and row_text.lower() != label.lower() and _durationish(row_text):
                        pruned = _clean(row_text.replace(label, "")).strip(": -—–")
                        if pruned and len(pruned) < 140:
                            info = pruned

                    items.append(f"• [{label}]({abs_href})" + (f" — {info}" if info else ""))
                    if len(items) >= max_items:
                        return items

        # Collect from lists
        if sib.name in ("ul", "ol"):
            for li in sib.find_all("li", recursive=False):
                for a in li.find_all("a", href=True):
                    label = _clean(a.get_text(" ", strip=True))
                    if not label or len(label) < 2:
                        continue
                    href = a.get("href", "")
                    if _bad_href(href):
                        continue
                    abs_href = urljoin(base_url, href)
                    key = (label.lower(), abs_href)
                    if key in seen:
                        continue
                    seen.add(key)

                    # Extract date info from list item
                    info = None
                    li_text = _clean(li.get_text(" ", strip=True))
                    if li_text and li_text.lower() != label.lower() and _durationish(li_text):
                        pruned = _clean(li_text.replace(label, "")).strip(": -—–")
                        if pruned and len(pruned) < 140:
                            info = pruned

                    items.append(f"• [{label}]({abs_href})" + (f" — {info}" if info else ""))
                    if len(items) >= max_items:
                        return items

    return items


def extract_wuwa_events(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Parse WuWa events page for ongoing and upcoming events."""
    bullets: List[str] = []
    root = _find_article_root(soup)
    headers = list(root.find_all(["h2", "h3", "h4"]))

    sections = [
        ("__Ongoing Events__", _WUWA_CURRENT_EVENTS_HINTS, ["upcoming"]),
        ("__Upcoming Events__", _WUWA_UPCOMING_EVENTS_HINTS, ["ongoing", "permanent"]),
    ]

    for title, hints, stop_on in sections:
        head = next((h for h in headers if _match_event_header(h, hints)), None)
        if head:
            rows = _collect_events_from_section(head, base_url, stop_on)
            if rows:
                bullets.append(title)
                bullets.extend(rows)

    if not bullets:
        return ["__Wuthering Waves Events__", "• _No events found._"]

    return bullets
