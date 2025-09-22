# -*- coding: utf-8 -*-
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

def extract_wuwa_gachas(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Parse the Wuthering Waves banner schedule page for current/active banners."""
    bullets: List[str] = ["__Current Banners / Gacha__"]
    heads = []
    for h in soup.find_all(["h2","h3","h4"]):
        t = _clean(h.get_text(" ", strip=True)).lower()
        if any(k in t for k in ["current banner", "ongoing banner", "current banners", "featured", "rate-up"]):
            heads.append(h)
    if not heads:
        heads = soup.find_all(["h2","h3"])

    seen = set()
    for head in heads[:2]:
        count = 0
        for sib in head.find_all_next():
            if sib is head:
                continue
            if getattr(sib, "name", None) in ["h2","h3"]:
                break
            blocks = []
            if sib.name in ["ul","ol"]:
                blocks.extend(sib.find_all("li", recursive=False))
            elif sib.name == "table":
                blocks.extend(sib.find_all("tr"))
            elif sib.name in ["p","div"]:
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
                bullets.append(f"• [{label}]({href})" + (f" — {info}" if info else ""))
                count += 1
                if count >= 8:
                    break
            if count >= 8:
                break

    if len(bullets) == 1:
        bullets.append("• _No parseable banners found (layout may have changed)._")
    return bullets
