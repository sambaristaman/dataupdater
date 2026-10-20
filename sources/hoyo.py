# -*- coding: utf-8 -*-
"""Genshin Impact and Honkai: Star Rail sources (ennead.cc + official announcements)."""

import html
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sources.common import (
    Item,
    SourceResult,
    http_get_json,
    parse_local,
    with_fallback,
)

ENNEAD_GENSHIN = "https://api.ennead.cc/mihoyo/genshin/calendar"
ENNEAD_HSR = "https://api.ennead.cc/mihoyo/starrail/calendar"

GI_ANN = (
    "https://sg-hk4e-api.hoyoverse.com/common/hk4e_global/announcement/api/getAnnList"
    "?game=hk4e&game_biz=hk4e_global&lang=en&bundle_id=hk4e_global&platform=pc"
    "&region=os_usa&level=55&uid=100000000&authkey_ver=1&sign_type=2"
)
SR_ANN = (
    "https://sg-hkrpg-api.hoyoverse.com/common/hkrpg_global/announcement/api/getAnnList"
    "?game=hkrpg&game_biz=hkrpg_global&lang=en&bundle_id=hkrpg_global&platform=pc"
    "&region=prod_official_usa&level=70&uid=600000000&authkey_ver=1&sign_type=2&channel_id=1"
)

MAX_OFFICIAL_DURATION = timedelta(days=60)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _from_ts(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), timezone.utc)


def ennead_events(payload: dict) -> List[Item]:
    items: List[Item] = []
    for ev in payload.get("events") or []:
        name = (ev.get("name") or "").strip()
        start, end = _from_ts(ev.get("start_time")), _from_ts(ev.get("end_time"))
        if not name or (start is None and end is None):
            continue
        items.append(Item(label=name, start=start, end=end))
    return items


def _names(entries, rarity: int) -> List[str]:
    return [e["name"] for e in entries if e.get("rarity") == rarity and e.get("name")]


def ennead_banners(payload: dict, default_name: str) -> List[Item]:
    items: List[Item] = []
    for b in payload.get("banners") or []:
        chars = b.get("characters") or []
        gear = b.get("weapons") or b.get("light_cones") or []
        entries = list(chars) + list(gear)
        five = _names(entries, 5)
        four = _names(entries, 4)
        if not five:
            continue
        name = (b.get("name") or "").strip()
        if not name:
            name = default_name
            if not chars:
                name = "Light Cone Warp" if default_name == "Event Warp" else "Weapon " + default_name
        details = f"5★ {', '.join(five)}"
        if four:
            details += f" · 4★ {', '.join(four)}"
        items.append(
            Item(
                label=f"{name}: {', '.join(five)}",
                start=_from_ts(b.get("start_time")),
                end=_from_ts(b.get("end_time")),
                details=details,
            )
        )
    return items


def _clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text or "", flags=re.I)
    text = _TAG_RE.sub("", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def official_items(payload: dict) -> List[Item]:
    data = payload.get("data") or {}
    offset = data.get("timezone")
    offset = -5 if offset is None else offset
    anns = [a for g in data.get("list") or [] for a in g.get("list") or []]
    for g in data.get("pic_list") or []:
        for t in g.get("type_list") or []:
            anns.extend(t.get("list") or [])
    items: List[Item] = []
    for a in anns:
        title = _clean(a.get("title"))
        if not title:
            continue
        start = parse_local(a.get("start_time") or "", offset)
        end = parse_local(a.get("end_time") or "", offset)
        # Undated or years-long notices (fair-use statement, top-up center...) aren't events.
        if start is None or end is None or end - start > MAX_OFFICIAL_DURATION:
            continue
        items.append(Item(label=title, start=start, end=end))
    return items


# --- Sources ---

def _ennead(url: str, parse) -> SourceResult:
    return SourceResult(parse(http_get_json(url)), "ennead.cc", url)


def _official(url: str) -> SourceResult:
    return SourceResult(official_items(http_get_json(url)), "hoyoverse announcements", url)


def _genshin_events_primary(now: datetime) -> SourceResult:
    return _ennead(ENNEAD_GENSHIN, ennead_events)


def _genshin_events_official(now: datetime) -> SourceResult:
    return _official(GI_ANN)


def _hsr_events_primary(now: datetime) -> SourceResult:
    return _ennead(ENNEAD_HSR, ennead_events)


def _hsr_events_official(now: datetime) -> SourceResult:
    return _official(SR_ANN)


def genshin_events(now: datetime) -> SourceResult:
    return with_fallback(_genshin_events_primary, _genshin_events_official)(now)


def hsr_events(now: datetime) -> SourceResult:
    return with_fallback(_hsr_events_primary, _hsr_events_official)(now)


def genshin_gacha(now: datetime) -> SourceResult:
    return _ennead(ENNEAD_GENSHIN, lambda p: ennead_banners(p, "Event Wish"))


def hsr_gacha(now: datetime) -> SourceResult:
    return _ennead(ENNEAD_HSR, lambda p: ennead_banners(p, "Event Warp"))
