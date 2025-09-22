# hsr_extractor.py

from typing import List
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

def _clean(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", s).strip()

def _durationish(s: str) -> bool:
    s = s or ""
    return any(k in s for k in ["Duration", "Event Duration", "期間", "to ", "–", "—", "-"]) or bool(re.search(r"\b\d{4}\b", s))

def _collect_section_items(head: Tag, base_url: str, max_items: int = 12) -> List[str]:
    items: List[str] = []
    seen = set()

    for sib in head.find_all_next():
        if sib is head:
            continue
        # stop when next major section begins
        if getattr(sib, "name", None) in ["h2", "h3"]:
            break

        blocks = []
        if sib.name in ["ul", "ol"]:
            blocks.extend(sib.find_all("li", recursive=False))
        elif sib.name == "table":
            blocks.extend(sib.find_all("tr"))
        elif sib.name in ["p", "div"]:
            blocks.append(sib)

        for blk in blocks:
            a = blk.find("a", href=True)
            if not a:
                continue
            label = _clean(a.get_text(" ", strip=True))
            if not label or len(label) < 2:
                continue
            href = urljoin(base_url, a["href"])
            key = (label.lower(), href)
            if key in seen:
                continue
            seen.add(key)
            info = None
            bt = _clean(blk.get_text(" ", strip=True))
            if bt and bt.lower() != label.lower() and _durationish(bt):
                if len(bt) > len(label)+3:
                    info = _clean(bt.replace(label, "")).strip(": -—–")
            items.append(f"• [{label}]({href})" + (f" — {info}" if info else ""))
            if len(items) >= max_items:
                return items

    return items

def extract_hsr_gachas(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Parse the HSR banner schedule page for current AND upcoming warps."""
    bullets: List[str] = []

    # 1) CURRENT
    current_heads = []
    for h in soup.find_all(["h2","h3","h4"]):
        t = _clean(h.get_text(" ", strip=True)).lower()
        if any(k in t for k in ["current banner", "current warp", "ongoing banner", "rate-up", "featured"]):
            current_heads.append(h)
    if not current_heads:
        current_heads = soup.find_all(["h2","h3"])

    seen = set()
    curr_section: List[str] = ["__Current Banners / Warps__"]
    got_any_current = False
    for head in current_heads[:2]:
        items = _collect_section_items(head, base_url, max_items=8)
        if items:
            got_any_current = True
            curr_section.extend(items)
            break
    if not got_any_current:
        curr_section.append("• _No parseable banners found (layout may have changed)._")
    bullets.extend(curr_section)

    # 2) UPCOMING
    upcoming_heads = []
    for h in soup.find_all(["h2","h3","h4"]):
        t = _clean(h.get_text(" ", strip=True)).lower()
        if any(k in t for k in ["upcoming banner", "upcoming warp", "future banner", "next banner", "upcoming rate-up", "upcoming"]):
            upcoming_heads.append(h)
    if upcoming_heads:
        up_items = _collect_section_items(upcoming_heads[0], base_url, max_items=12)
        if up_items:
            bullets.append("__Upcoming Banners / Warps__")
            bullets.extend(up_items)

    return bullets
