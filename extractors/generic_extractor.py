# -*- coding: utf-8 -*-
import re
from typing import List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

# Local helpers (kept self-contained)

def _clean(s: str) -> str:
    import re as _re
    s = _re.sub(r"\s+", " ", s).strip()
    return s.replace("**", "").replace("__", "").replace("`", "")

def _durationish(s: str) -> bool:
    return any(k in s for k in ["Duration", "Event Duration", "期間", "to ", "–", "—", "-"]) or bool(re.search(r"\b\d{4}\b", s))

def _anchor_text(a: Tag) -> str:
    return _clean(a.get_text(" ", strip=True))

def _bad_href(u: str) -> bool:
    if not u:
        return True
    ul = u.lower().strip()
    if ul.startswith("javascript:") or ul.startswith("mailto:") or ul.endswith("#"):
        return True
    if any(p in ul for p in ["/login", "/register", "/signup", "/account"]):
        return True
    return False

def _collect_items_near_head(head: Tag, base_url: str, max_items: int = 12) -> List[str]:
    items: List[str] = []
    seen_links = set()

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
            if _bad_href(href):
                continue

            label = _anchor_text(a)
            if not label or len(label) < 2:
                continue

            abs_href = urljoin(base_url, href)
            key = (label.lower(), abs_href)
            if key in seen_links:
                continue
            seen_links.add(key)

            info: Optional[str] = None
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

def extract_events_with_links_generic(soup: BeautifulSoup, base_url: str) -> List[str]:
    headings = soup.find_all(["h2", "h3", "h4"])
    key_heads = [h for h in headings if any(
        t in h.get_text(strip=True).lower()
        for t in ["current events", "ongoing events", "events calendar", "upcoming", "featured events", "new archives", "upcoming archives"]
    )]

    bullets: List[str] = []
    if key_heads:
        for head in key_heads:
            title = _clean(head.get_text(" ", strip=True))
            bullets.append(f"__{title}__")
            bullets.extend(_collect_items_near_head(head, base_url, max_items=10))
    else:
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if _bad_href(href):
                continue
            label = _anchor_text(a)
            if not label or len(label) < 3:
                continue
            abs_href = urljoin(base_url, href)
            key = (label.lower(), abs_href)
            if key in seen:
                continue
            info = None
            parent = a.find_parent(["li", "p", "div", "tr"])
            if parent:
                pt = _clean(parent.get_text(" ", strip=True))
                if _durationish(pt) and len(pt) < 160:
                    info = pt
            line = f"• [{label}]({abs_href})" + (f" — {info}" if info else "")
            bullets.append(line)
            seen.add(key)
            if len(bullets) >= 12:
                break

    final: List[str] = []
    seen_line = set()
    for b in bullets:
        if b not in seen_line:
            final.append(b)
            seen_line.add(b)
    return final[:40]
