# -*- coding: utf-8 -*-
"""
Arknights: Endfield events and banners extractor for Game8.
"""

from typing import List, Optional
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag


# ---------- helpers ----------

def _clean(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", (s or "")).strip()


def _durationish(s: str) -> bool:
    """Check if a string looks like a date/duration."""
    s = s or ""
    return (
        any(k in s for k in ["Duration", "Event Duration", " to ", "–", "—", "-"])
        or bool(re.search(r"\b\d{4}\b", s))
        or bool(re.search(r"\b\d{1,2}/\d{1,2}\b", s))
        or bool(re.search(r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s*\d{4}", s))
    )


def _bad_href(u: str) -> bool:
    if not u:
        return True
    ul = u.lower().strip()
    if ul.startswith(("javascript:", "mailto:")) or ul.endswith("#"):
        return True
    if any(p in ul for p in ["/login", "/register", "/signup", "/account", "site-interface"]):
        return True
    return False


def _is_header(tag: Tag) -> bool:
    return getattr(tag, "name", "").lower() in {"h2", "h3", "h4"}


def _header_text(tag: Tag) -> str:
    return _clean(tag.get_text(" ", strip=True)).lower()


def _match_header(tag: Tag, hints: List[str]) -> bool:
    t = _header_text(tag)
    return any(h in t for h in hints)


# Section hints for events
_CURRENT_EVENTS_HINTS = [
    "current events", "current event", "ongoing events", "ongoing event",
]
_UPCOMING_EVENTS_HINTS = [
    "upcoming events", "upcoming event", "future events",
]

# Section hints for banners/gacha
_CURRENT_BANNERS_HINTS = [
    "current banners", "current banner", "ongoing banners", "active banners",
]
_NEXT_BANNERS_HINTS = [
    "next banners", "next banner",
]
_UPCOMING_BANNERS_HINTS = [
    "upcoming banners", "upcoming banner", "future banners",
]

# Stop words for section scanning
_EVENT_STOP_HINTS = [
    "related guides", "comment", "permanent", "history", "all events",
]
_BANNER_STOP_HINTS = [
    "related guides", "comment", "permanent banners", "standard banner",
    "history", "all banners", "gacha simulator",
]


def _extract_date_from_text(text: str) -> Optional[str]:
    """Extract date range from text if present."""
    # Format: "Jan. 22 - Feb. 7, 2026" or "01/22 - 02/07"
    month_pattern = r"[A-Za-z]{3,9}\.?\s+\d{1,2}"

    # Full date range with year: "Jan. 22 - Feb. 7, 2026"
    m = re.search(rf"({month_pattern})\s*[-–—]\s*({month_pattern}),?\s*\d{{4}}", text)
    if m:
        return m.group(0)

    # Numeric date range: "01/22 - 02/07"
    m = re.search(r"(\d{1,2}/\d{1,2})\s*[-–—]\s*(\d{1,2}/\d{1,2})", text)
    if m:
        return f"{m.group(1)} - {m.group(2)}"

    # Full date with year range: "Jan 22, 2026 - Feb 7, 2026"
    m = re.search(rf"({month_pattern},?\s*\d{{4}})\s*[-–—]\s*({month_pattern},?\s*\d{{4}})", text)
    if m:
        return m.group(0)

    return None


def _collect_events_from_section(head: Tag, base_url: str, stop_hints: List[str], max_items: int = 12) -> List[str]:
    """Walk from header collecting event links with dates."""
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue

        # Stop at next major header that looks like a new section
        if _is_header(sib):
            t = _header_text(sib)
            if any(x in t for x in stop_hints + _EVENT_STOP_HINTS):
                break
            # Also stop at other known section headers
            if any(x in t for x in ["upcoming events", "current events"]) and t != _header_text(head):
                break

        # Scan common containers
        blocks = []
        if sib.name in ("ul", "ol"):
            blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            blocks.extend(sib.find_all("tr"))
        elif sib.name in ("div", "p"):
            blocks.append(sib)

        for blk in blocks:
            for a in blk.find_all("a", href=True):
                label = _clean(a.get_text(" ", strip=True))
                if not label or len(label) < 3:
                    continue
                href = a.get("href", "")
                if _bad_href(href):
                    continue

                abs_href = urljoin(base_url, href)
                key = (label.lower(), abs_href)
                if key in seen:
                    continue
                seen.add(key)

                # Try to extract date from surrounding text
                info = None
                bt = _clean(blk.get_text(" ", strip=True))
                if bt and len(bt) < 200:
                    date_str = _extract_date_from_text(bt)
                    if date_str:
                        info = date_str
                    elif _durationish(bt) and bt.lower() != label.lower():
                        pruned = _clean(bt.replace(label, "")).strip(": -—–")
                        if pruned and len(pruned) < 80:
                            info = pruned

                line = f"• [{label}]({abs_href})" + (f" — {info}" if info else "")
                items.append(line)

                if len(items) >= max_items:
                    return items

    return items


def _collect_banners_from_section(head: Tag, base_url: str, stop_hints: List[str], max_items: int = 12) -> List[str]:
    """Walk from header collecting banner links with dates."""
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue

        # Stop at next major header that looks like a new section
        if _is_header(sib):
            t = _header_text(sib)
            if any(x in t for x in stop_hints + _BANNER_STOP_HINTS):
                break

        # Scan common containers
        blocks = []
        if sib.name in ("ul", "ol"):
            blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            blocks.extend(sib.find_all("tr"))
        elif sib.name in ("div", "p"):
            blocks.append(sib)

        for blk in blocks:
            for a in blk.find_all("a", href=True):
                label = _clean(a.get_text(" ", strip=True))
                if not label or len(label) < 3:
                    continue
                href = a.get("href", "")
                if _bad_href(href):
                    continue

                abs_href = urljoin(base_url, href)
                key = (label.lower(), abs_href)
                if key in seen:
                    continue
                seen.add(key)

                # Try to extract date from surrounding text
                info = None
                bt = _clean(blk.get_text(" ", strip=True))
                if bt and len(bt) < 200:
                    date_str = _extract_date_from_text(bt)
                    if date_str:
                        info = date_str
                    elif _durationish(bt) and bt.lower() != label.lower():
                        pruned = _clean(bt.replace(label, "")).strip(": -—–")
                        if pruned and len(pruned) < 100:
                            info = pruned

                line = f"• [{label}]({abs_href})" + (f" — {info}" if info else "")
                items.append(line)

                if len(items) >= max_items:
                    return items

    return items


# ---------- main entry points ----------

def extract_endfield_events(soup: BeautifulSoup, base_url: str) -> List[str]:
    """
    Parse the Game8 Arknights: Endfield events page for:
    - Current Events
    - Upcoming Events
    Returns a list of markdown bullets grouped by section headers.
    """
    bullets: List[str] = []
    headers = list(soup.find_all(["h2", "h3", "h4"]))

    if not headers:
        return ["__Arknights: Endfield Events__", "• _No headers found (layout may have changed)._"]

    sections = [
        ("__Current Events__", _CURRENT_EVENTS_HINTS, ["upcoming"]),
        ("__Upcoming Events__", _UPCOMING_EVENTS_HINTS, ["current"]),
    ]

    for title, hints, stop_on in sections:
        head = next((h for h in headers if _match_header(h, hints)), None)
        if not head:
            continue
        rows = _collect_events_from_section(head, base_url, stop_on)
        if rows:
            bullets.append(title)
            seen = set()
            for r in rows:
                if r not in seen:
                    bullets.append(r)
                    seen.add(r)

    if not bullets:
        bullets = ["__Arknights: Endfield Events__", "• _No parseable events found (layout may have changed)._"]

    return bullets


def extract_endfield_gachas(soup: BeautifulSoup, base_url: str) -> List[str]:
    """
    Parse the Game8 Arknights: Endfield banner schedule page for:
    - Current Banners
    - Next Banners
    - Upcoming Banners (skip Permanent)
    Returns a list of markdown bullets grouped by section headers.
    """
    bullets: List[str] = []
    headers = list(soup.find_all(["h2", "h3", "h4"]))

    if not headers:
        return ["__Arknights: Endfield Banners__", "• _No headers found (layout may have changed)._"]

    sections = [
        ("__Current Banners__", _CURRENT_BANNERS_HINTS, ["next", "upcoming"]),
        ("__Next Banners__", _NEXT_BANNERS_HINTS, ["current", "upcoming"]),
        ("__Upcoming Banners__", _UPCOMING_BANNERS_HINTS, ["current", "next"]),
    ]

    for title, hints, stop_on in sections:
        head = next((h for h in headers if _match_header(h, hints)), None)
        if not head:
            continue
        rows = _collect_banners_from_section(head, base_url, stop_on)
        if rows:
            bullets.append(title)
            seen = set()
            for r in rows:
                if r not in seen:
                    bullets.append(r)
                    seen.add(r)

    if not bullets:
        bullets = ["__Arknights: Endfield Banners__", "• _No parseable banners found (layout may have changed)._"]

    return bullets
