# -*- coding: utf-8 -*-
from typing import List, Optional
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

# ---------- helpers ----------

def _clean(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", (s or "")).strip()

def _durationish(s: str) -> bool:
    s = s or ""
    # catches ranges like "Sep. 23, 2025 - Oct. 15, 2025", "09/23 - 10/15/2025", "to", dashes, and years
    return (
            any(k in s for k in ["Duration", "Event Duration", " to ", "–", "—", "-"])
            or bool(re.search(r"\b\d{4}\b", s))
            or bool(re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", s))
            or bool(re.search(r"[A-Za-z]{3,9}\.? \d{1,2}, ?\d{4}", s))
    )

def _is_header(tag: Tag) -> bool:
    return getattr(tag, "name", "").lower() in {"h2", "h3", "h4"}

def _header_text(tag: Tag) -> str:
    return _clean(tag.get_text(" ", strip=True)).lower()

_CURRENT_HINTS = [
    "current warp", "current banner", "current banners", "current warp banners",
    "current", "now available",
]
_NEXT_HINTS = [
    "next banner", "next banners", "hsr next banner", "next light cone", "next warp",
]
_UPCOMING_HINTS = [
    "upcoming warp banners", "upcoming banners", "upcoming warps", "upcoming",
]

def _match_header(tag: Tag, hints: list[str]) -> bool:
    t = _header_text(tag)
    return any(h in t for h in hints)

def _block_text_without_link(blk: Tag, label: str) -> Optional[str]:
    bt = _clean(blk.get_text(" ", strip=True))
    if not bt:
        return None
    if bt.lower() == label.lower():
        return None
    # strip the label to leave dates / notes
    pruned = _clean(bt.replace(label, "")).strip(": -—–")
    return pruned or None

def _collect_links_from_section(head: Tag, base_url: str, stop_on: list[str], max_items: int = 14) -> List[str]:
    """Walk forward from header, collecting banner-ish links + nearby date/info text."""
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue

        # stop at next major header that looks like a new section
        if _is_header(sib):
            t = _header_text(sib)
            if any(x in t for x in stop_on + ["related guides", "all warp (gacha) simulators", "permanent", "history"]):
                break

        # scan common containers
        blocks = []
        if sib.name in ("ul", "ol"):
            blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            blocks.extend(sib.find_all("tr"))
        elif sib.name in ("div", "p"):
            blocks.append(sib)

        for blk in blocks:
            a_tags = blk.find_all("a", href=True)
            if not a_tags:
                continue
            # prefer anchor texts that look like banner entries
            for a in a_tags:
                label = _clean(a.get_text(" ", strip=True))
                if not label or len(label) < 2:
                    continue
                # keep it banner-focused
                low = label.lower()
                if not any(k in low for k in ["banner", "light cone", "brilliant fixation", "bygone reminiscence", "warp"]):
                    # allow named characters in "Next Banner Information"
                    if not any(k in low for k in ["evernight", "herta", "permansor", "anaxa", "saber", "archer", "silver wolf", "kafka", "cerydra"]):
                        continue

                href = urljoin(base_url, a["href"])
                key = (label.lower(), href)
                if key in seen:
                    continue
                seen.add(key)

                info = _block_text_without_link(blk, label)
                if info and not _durationish(info):
                    # try to find a nearby date within the same block text
                    # formats like "Sep. 23, 2025 - Oct. 15, 2025" or "09/23 - 10/15/2025"
                    m = re.search(r"([A-Za-z]{3,9}\.? \d{1,2}, ?\d{4}.*?\d{4}|\d{1,2}/\d{1,2} ?- ?\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{1,2}/\d{2,4})", info)
                    info = m.group(0) if m else info

                line = f"• [{label}]({href})" + (f" — {info}" if info and _durationish(info) else "")
                items.append(line)

                if len(items) >= max_items:
                    return items

    return items

# ---------- main entry ----------

def extract_hsr_gachas(soup: BeautifulSoup, base_url: str) -> List[str]:
    """
    Parses the Game8 'All Current and Upcoming Warp Banners Schedule' page for:
    - Current banners
    - Next Banner (e.g., 'HSR Next Banner in Version 3.6')
    - Upcoming Warp Banners (future phases)
    Returns a list of markdown bullets grouped by headers.
    """
    bullets: List[str] = []

    headers = list(soup.find_all(["h2", "h3", "h4"]))
    if not headers:
        return ["__Honkai: Star Rail Banners__", "• _No headers found (layout may have changed)._"]

    # sections we’ll try to pull (in this order)
    sections = [
        ("__Current Warp Banners__", _CURRENT_HINTS, ["next", "upcoming"]),
        ("__Next Banners__", _NEXT_HINTS, ["upcoming", "current", "light cone banners", "countdown"]),
        ("__Upcoming Warp Banners__", _UPCOMING_HINTS, ["current", "next"]),
    ]

    for title, hints, stop_on in sections:
        head = next((h for h in headers if _match_header(h, hints)), None)
        if not head:
            continue
        rows = _collect_links_from_section(head, base_url, stop_on=stop_on)
        if rows:
            bullets.append(title)
            # de-dup while preserving order
            seen = set()
            for r in rows:
                if r not in seen:
                    bullets.append(r)
                    seen.add(r)

    if not bullets:
        bullets = ["__Honkai: Star Rail Banners__", "• _No parseable banners found (layout may have changed)._"]

    return bullets
