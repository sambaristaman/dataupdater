# -*- coding: utf-8 -*-
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
    # Keep parsing constrained to the article/main content area
    for cand in [
        soup.find(id=re.compile(r"(article|content).*(body|main)", re.I)),
        soup.find(class_=re.compile(r"(article|content).*(body|main)", re.I)),
        soup.find("article"),
        soup.find("main"),
    ]:
        if isinstance(cand, Tag):
            return cand
    return soup

# Patterns we consider as the "current banners" section on Game8 WuWa
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

# Heads that indicate the NEXT section boundary
_SECTION_BREAK_HINTS = [
    "upcoming banners",
    "permanent banners",
    "all convene (gacha) simulators",
    "all wuwa version banner history",
    "comment",
    "related guides",
]

# Light sanity check to reject obvious site-chrome/junk hrefs
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
    # Scan a modest window after the section for a date range line
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
        # Also accept short duration lines (kept brief)
        if _durationish(text) and 8 < len(text) < 80 and re.search(r"\b\d{4}\b", text):
            return text
    return None

def _collect_links_in_section(head: Tag, base_url: str, max_items: int = 12) -> List[str]:
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue
        # Stop when a new major section starts
        if getattr(sib, "name", "") in ("h2", "h3") and _is_section_break(sib):
            break

        # Consider common block containers near Game8 tables/cards
        blocks: List[Tag] = []
        if sib.name in ("ul", "ol"):
            blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            blocks.extend(sib.find_all("tr"))
        elif sib.name in ("div", "p"):
            blocks.append(sib)

        for blk in blocks:
            # Collect multiple anchors per block — character banners + weapon banners
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

                # Try to pull short context from the immediate block text (exclude duplicate label)
                info = None
                bt = _clean(blk.get_text(" ", strip=True))
                if bt and bt.lower() != label.lower() and _durationish(bt):
                    # avoid echoing the label twice
                    pruned = _clean(bt.replace(label, "")).strip(": -—–")
                    if pruned and pruned != label and len(pruned) < 140:
                        info = pruned

                items.append(f"• [{label}]({abs_href})" + (f" — {info}" if info else ""))
                if len(items) >= max_items:
                    return items

    return items

def extract_wuwa_gachas(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Parse the Wuthering Waves banner schedule page for current/active banners."""
    bullets: List[str] = ["__Current Banners / Gacha__"]

    root = _find_article_root(soup)

    # Find the first heading that looks like the "current/available" section
    heads: List[Tag] = []
    for h in root.find_all(["h2", "h3", "h4"]):
        t = _clean(h.get_text(" ", strip=True)).lower()
        if any(k in t for k in _CURRENT_HEAD_HINTS):
            heads.append(h)
    # Fallback: use the first H2 on the page if nothing matched (keeps old behavior)
    if not heads:
        heads = list(root.find_all(["h2", "h3"])[:1])

    if not heads:
        # Ultra-conservative fallback: give a friendly failure line
        return bullets + ["• _No parseable banners found (layout may have changed)._"]

    # We only need the first "current/available" section
    head = heads[0]

    # Pull a shared date range once (applies to all current banners shown together)
    shared_dates = _gather_date_near(head)

    # Collect anchors for banners under this section
    items = _collect_links_in_section(head, base_url, max_items=12)

    # Heuristic: keep only likely banner titles (contain known banner words or are capitalized phrases)
    NICE = []
    for line in items:
        # keep Absolute Pulsation / weapon/character banner names / featured names
        if any(k in line.lower() for k in ["banner", "convene", "pulsation", "waxes", "rhythms"]):
            NICE.append(line)

    chosen = NICE or items

    # If we found a common date window, append it to items missing info
    if shared_dates:
        fixed = []
        for line in chosen:
            if " — " in line:
                fixed.append(line)  # already has info
            else:
                fixed.append(f"{line} — {shared_dates}")
        chosen = fixed

    # Safety: de-dup while preserving order
    final: List[str] = []
    seen = set()
    for b in chosen:
        if b not in seen:
            final.append(b)
            seen.add(b)

    if len(final) == 0:
        bullets.append("• _No parseable banners found (layout may have changed)._")
    else:
        bullets.extend(final[:12])

    return bullets
