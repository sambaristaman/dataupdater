# -*- coding: utf-8 -*-
"""Wuthering Waves events and convenes (banners) from the Fandom wiki."""

import re
from datetime import datetime
from typing import Dict, List, Optional

from sources.common import Item, SourceResult, parse_local, parse_offset
from sources.mediawiki import category_members, page_wikitexts, parse_template

API = "https://wutheringwaves.fandom.com/api.php"
WIKI = "https://wutheringwaves.fandom.com/wiki/"
SOURCE_NAME = "wutheringwaves.fandom.com"

CAT_EVENTS = "Category:Events"
CAT_RESONATOR = "Category:Featured_Resonator_Convenes"
CAT_WEAPON = "Category:Featured_Weapon_Convenes"

_DATE_SUFFIX_RE = re.compile(r"/\d{4}-\d{2}-\d{2}$")


def _result(items: List[Item]) -> SourceResult:
    return SourceResult(items=items, source_name=SOURCE_NAME, source_url=WIKI)


def _offset(value: Optional[str]) -> Optional[float]:
    """Parse a wiki offset ('UTC+8', typo 'UCT-7'); None when blank/unparseable."""
    value = (value or "").strip().replace("UCT", "UTC")
    if not value:
        return None
    parsed = parse_offset(value, default=float("nan"))
    return None if parsed != parsed else parsed


def _date(value: Optional[str], offset: float) -> Optional[datetime]:
    """Parse a wiki date; blank or markers like 'none'/'no'/'TBA' give None."""
    try:
        return parse_local((value or "").strip(), offset)
    except ValueError:
        return None


# |time_end=none/permanent marks permanent content; |time_known=no or |time_end=no marks
# recurring content without a fixed window. Neither is a dated event. (A genuinely blank
# |time_end= just means the wiki hasn't filled it in yet and is kept as start-only.)
_PERMANENT_END = {"none", "permanent", "no"}


def _undated(params: Dict[str, str]) -> bool:
    known = (params.get("time_known") or "").strip().lower()
    end = (params.get("time_end") or "").strip().lower()
    return known == "no" or end in _PERMANENT_END


def _dates(params: Dict[str, str]):
    start_off = _offset(params.get("time_start_offset"))
    end_off = _offset(params.get("time_end_offset"))
    if start_off is None:
        start_off = end_off if end_off is not None else 8
    if end_off is None:
        end_off = start_off
    return (
        _date(params.get("time_start"), start_off),
        _date(params.get("time_end"), end_off),
    )


def _fetch(categories: List[str]) -> Dict[str, str]:
    titles: List[str] = []
    for cat in categories:
        for t in category_members(API, cat):
            if t not in titles:
                titles.append(t)
    return page_wikitexts(API, titles) if titles else {}


def _split(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def wuwa_events(now: datetime) -> SourceResult:
    items: List[Item] = []
    for title, text in _fetch([CAT_EVENTS]).items():
        ev = parse_template(text, "Event")
        if ev is None or _undated(ev):
            continue
        start, end = _dates(ev)
        if start is None and end is None:
            continue
        label = (ev.get("name") or "").strip() or title
        link = (ev.get("link") or "").strip() or WIKI + title.replace(" ", "_")
        items.append(Item(label=label, start=start, end=end, link=link))
    return _result(items)


def wuwa_gacha(now: datetime) -> SourceResult:
    items: List[Item] = []
    for title, text in _fetch([CAT_RESONATOR, CAT_WEAPON]).items():
        conv = parse_template(text, "Convene")
        if conv is None or _undated(conv):
            continue
        pool = parse_template(text, "Convene/Pool") or {}
        five = _split(pool.get("resonator_5_F")) + _split(pool.get("weapon_5_F"))
        four = _split(pool.get("resonator_4_F")) + _split(pool.get("weapon_4_F"))
        if not five:
            continue
        start, end = _dates(conv)
        if start is None and end is None:
            continue
        details = f"5★ {', '.join(five)}"
        if four:
            details += f" · 4★ {', '.join(four)}"
        link = (conv.get("link") or "").strip() or WIKI + title.replace(" ", "_")
        items.append(
            Item(
                label=_DATE_SUFFIX_RE.sub("", title),
                start=start,
                end=end,
                link=link,
                details=details,
            )
        )
    return _result(items)
