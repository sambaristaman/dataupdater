# -*- coding: utf-8 -*-
import re
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

# --- Local helpers ---

def _clean(s: str) -> str:
    import re as _re
    s = _re.sub(r"\s+", " ", s).strip()
    return s.replace("**", "").replace("__", "").replace("`", "")

_SKIP_TEXT_PATTERNS = [
    "create your free account",
    "save articles to your watchlist",
    "save your favorite games",
    "receive instant notifications",
    "convenient features in the comments",
    "site interface",
    "game tools",
]

def _is_junk_text(s: str) -> bool:
    s_low = s.lower()
    return any(p in s_low for p in _SKIP_TEXT_PATTERNS)

def _is_good_genshin_url(u: str) -> bool:
    u = u.lower()
    if "genshin-impact" not in u:
        return False
    if any(x in u for x in ["/account", "/login", "/register", "/tools", "site-interface"]):
        return False
    return True

def _find_section_roots(soup: BeautifulSoup, titles: List[str]) -> List[Tag]:
    roots = []
    tlow = [t.lower() for t in titles]
    for h in soup.find_all(["h2", "h3"]):
        txt = _clean(h.get_text(" ", strip=True)).lower()
        if txt in tlow:
            roots.append(h)
    if roots:
        return roots
    for a in soup.find_all("a"):
        txt = _clean(a.get_text(" ", strip=True)).lower()
        if txt in tlow:
            roots.append(a)
    return roots

def _find_nearby_link_for_event(head: Tag, base_url: str) -> Optional[str]:
    name = _clean(head.get_text(" ", strip=True)).lower()
    for sib in head.find_all_next(limit=40):
        if sib is head:
            continue
        if sib.name == "h3":  # next event block starts
            break
        for a in sib.find_all("a", href=True):
            label = _clean(a.get_text(" ", strip=True)).lower()
            href = urljoin(base_url, a["href"])
            if not _is_good_genshin_url(href):
                continue
            if "guide" in label or name.split("—")[0].strip() in label:
                return href
    return None

_DATE_WORD = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s*\d{4}"
_DATE_RANGE = re.compile(r"(\d{1,2}/\d{1,2})\s*[-–—]\s*(\d{1,2}/\d{1,2})", re.I)
_DATE_LINE = re.compile(r"(event start|event end)[:\s]*(" + _DATE_WORD + ")", re.I)

def _collect_dates_after(head: Tag) -> Optional[str]:
    start = end = None
    compact = None
    for sib in head.find_all_next(limit=20):
        if sib is head:
            continue
        if sib.name == "h3":
            break
        t = _clean(sib.get_text(" ", strip=True))
        if not t:
            continue
        if _is_junk_text(t):
            continue
        m = _DATE_RANGE.search(t)
        if m:
            compact = f"{m.group(1)} - {m.group(2)}"
            break
        for part in t.split(" / "):
            m2 = _DATE_LINE.search(part)
            if m2:
                kind = m2.group(1).lower()
                date_str = m2.group(2)
                if "start" in kind and not start:
                    start = date_str
                elif "end" in kind and not end:
                    end = date_str
        if start and end:
            break

    if compact:
        return compact
    if start or end:
        return f"Start {start}" if start and not end else (f"End {end}" if end and not start else f"{start} → {end}")
    return None

# --- Public API ---

def extract_genshin_events(soup: BeautifulSoup, base_url: str) -> List[str]:
    SECTION_TITLES = ["List of Current Events", "List of Upcoming Events"]
    section_roots = _find_section_roots(soup, SECTION_TITLES)
    if not section_roots:
        return []  # fall back to generic

    bullets: List[str] = ["__List of Current/Upcoming Events__"]
    seen = set()

    def is_section_title(tag: Tag) -> bool:
        if not hasattr(tag, "get_text"):
            return False
        txt = _clean(tag.get_text(" ", strip=True)).lower()
        return txt in {t.lower() for t in SECTION_TITLES}

    for root in section_roots:
        for sib in root.next_siblings:
            from bs4 import Tag as _Tag
            if not isinstance(sib, _Tag):
                continue
            if sib.name in ("h2", "h3") and is_section_title(sib) and sib is not root:
                break
            if sib.name == "h3":
                txt = _clean(sib.get_text(" ", strip=True))
                low = txt.lower()
                if any(k in low for k in ["events calendar", "new archives", "upcoming archives"]):
                    continue
                if "version" in low and "event" in low:
                    continue
                if _is_junk_text(low) or len(txt.split()) < 2:
                    continue
                link = _find_nearby_link_for_event(sib, base_url)
                if link and not _is_good_genshin_url(link):
                    link = None
                dates = _collect_dates_after(sib)
                key = (low, link or "")
                if key in seen:
                    continue
                seen.add(key)
                if link and dates:
                    bullets.append(f"• [{txt}]({link}) — {dates}")
                elif link:
                    bullets.append(f"• [{txt}]({link})")
                elif dates:
                    bullets.append(f"• {txt} — {dates}")
                else:
                    bullets.append(f"• {txt}")
                if len(bullets) >= 14:
                    return bullets
            for h in sib.find_all("h3"):
                txt = _clean(h.get_text(" ", strip=True))
                low = txt.lower()
                if any(k in low for k in ["events calendar", "new archives", "upcoming archives"]):
                    continue
                if "version" in low and "event" in low:
                    continue
                if _is_junk_text(low) or len(txt.split()) < 2:
                    continue
                link = _find_nearby_link_for_event(h, base_url)
                if link and not _is_good_genshin_url(link):
                    link = None
                dates = _collect_dates_after(h)
                key = (low, link or "")
                if key in seen:
                    continue
                seen.add(key)
                if link and dates:
                    bullets.append(f"• [{txt}]({link}) — {dates}")
                elif link:
                    bullets.append(f"• [{txt}]({link})")
                elif dates:
                    bullets.append(f"• {txt} — {dates}")
                else:
                    bullets.append(f"• {txt}")
                if len(bullets) >= 14:
                    return bullets

    return bullets
