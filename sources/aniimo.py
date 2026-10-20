# -*- coding: utf-8 -*-
"""Aniimo: events from the official update notices (no banner source)."""

import re
from datetime import datetime
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from news_scraper import aniimo_detail, aniimo_list
from sources.common import Item, SourceResult, parse_local, parse_offset

SOURCE_NAME = "aniimo.com"
SOURCE_URL = "https://aniimo.com/en/newslist"
DETAIL_URL = "https://aniimo.com/en/newslist/detail/{}"
TITLE_RE = re.compile(r"Update Notice", re.I)
MAX_NOTICES = 6
PREFERRED_REGION = "Americas"

_HEADER_RE = re.compile(r"^[「\[](?:Event Period|Opening Time)[」\]]$|^\[(?:Event Period|Opening Time)\]$", re.I)
_REGION_RE = re.compile(
    r"[「\[]Aniimo-(?P<region>\w+)[」\]]\s*"
    r"(?P<s>\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*-\s*"
    r"(?P<e>\d{4}/\d{2}/\d{2} \d{2}:\d{2})\s*"
    r"(?P<tz>UTC[+-]?\d*)"
)
_REGION_PREFIX_RE = re.compile(r"^[「\[]Aniimo-")


def _lines(content_html: str) -> List[str]:
    soup = BeautifulSoup(content_html or "", "html.parser")
    return [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]


def aniimo_parse_events(content_html: str) -> List[Item]:
    lines = _lines(content_html)
    items: List[Item] = []
    for idx, line in enumerate(lines):
        if not _HEADER_RE.match(line):
            continue
        name: Optional[str] = None
        for prev in reversed(lines[:idx]):
            if not prev.startswith("[") and not prev.startswith(">>"):
                name = prev
                break
        regions: List[re.Match] = []
        has_region_line = False
        subtitle: Optional[str] = None
        for nxt in lines[idx + 1 :]:
            if nxt.startswith("[") and not _REGION_PREFIX_RE.match(nxt):
                break
            if _REGION_PREFIX_RE.match(nxt):
                has_region_line = True
                m = _REGION_RE.search(nxt)
                if m:
                    regions.append(m)
            elif has_region_line or regions:
                break
            elif subtitle is None:
                subtitle = nxt
            else:
                break
        if not regions:
            continue  # recurring, unlock-only or start-only blocks
        chosen = next((m for m in regions if m["region"] == PREFERRED_REGION), regions[0])
        # a subtitle line before the region lines is the specific event name
        label = subtitle or name
        if not label:
            continue
        try:
            offset = parse_offset(chosen["tz"], default=0)
            items.append(
                Item(label=label, start=parse_local(chosen["s"], offset), end=parse_local(chosen["e"], offset))
            )
        except ValueError:
            continue
    return items


def aniimo_events(now: datetime) -> SourceResult:
    notices = [a for a in aniimo_list() if TITLE_RE.search(a.get("title") or "")]
    notices.sort(key=lambda a: a.get("publishTime") or "", reverse=True)
    by_label: Dict[str, Item] = {}
    for art in notices[:MAX_NOTICES]:  # newest first: the first sighting wins
        try:
            found = aniimo_parse_events(aniimo_detail(art["id"]).get("content") or "")
        except Exception:
            continue
        link = DETAIL_URL.format(art["id"])
        for it in found:
            by_label.setdefault(it.label, Item(label=it.label, start=it.start, end=it.end, link=link))
    return SourceResult(items=list(by_label.values()), source_name=SOURCE_NAME, source_url=SOURCE_URL)
