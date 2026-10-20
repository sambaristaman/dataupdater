# -*- coding: utf-8 -*-
"""Umamusume: Pretty Derby (global) events and gacha banners from GameTora data files."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sources.common import Item, SourceResult, http_get_json

BASE = "https://gametora.com/data/umamusume"
MANIFEST_URL = "https://gametora.com/data/manifests/umamusume.json"
GACHA_LINK = "https://gametora.com/umamusume/gacha/history?server=en"
EVENTS_LINK = "https://gametora.com/umamusume/events"
SOURCE_NAME = "gametora.com"

_MANIFEST: Optional[Dict[str, str]] = None


def gametora_file(key: str) -> Any:
    """Fetch a hashed data file, resolving its hash through the (cached) manifest."""
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = http_get_json(MANIFEST_URL)
    return http_get_json(f"{BASE}/{key}.{_MANIFEST[key]}.json")


def _dt(ts: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


def _result(items: List[Item], link: str) -> SourceResult:
    return SourceResult(items=items, source_name=SOURCE_NAME, source_url=link)


def uma_events(now: datetime) -> SourceResult:
    items: List[Item] = []
    for key, prefix, name_field in (
        ("en/events/champions-meeting", "Champions Meeting", "name"),
        ("en/events/legend-race", "Legend Race", "name_en"),
    ):
        for ev in gametora_file(key):
            name = (ev.get(name_field) or "").strip()
            if name:
                items.append(
                    Item(
                        label=f"{prefix}: {name}",
                        start=_dt(ev.get("start")),
                        end=_dt(ev.get("end")),
                        link=EVENTS_LINK,
                    )
                )
    return _result(items, EVENTS_LINK)


def _names(pickups: List[list], names: Dict[int, str]) -> str:
    return ", ".join(names.get(p[0], f"#{p[0]}") for p in pickups)


def uma_gacha(now: datetime) -> SourceResult:
    chars = {
        c["card_id"]: c["name_en"] for c in gametora_file("character-cards") if c.get("name_en")
    }
    supports = {
        s["support_id"]: s["char_name"] for s in gametora_file("support-cards") if s.get("char_name")
    }
    items: List[Item] = []
    for key, prefix, names in (
        ("en/gacha/char-standard", "Character Banner", chars),
        ("en/gacha/support-standard", "Support Banner", supports),
    ):
        for banner in gametora_file(key):
            pickups = banner.get("pickups") or []
            if not pickups:
                continue
            items.append(
                Item(
                    label=f"{prefix}: {_names(pickups, names)}",
                    start=_dt(banner.get("start")),
                    end=_dt(banner.get("end")),
                    link=GACHA_LINK,
                )
            )
    return _result(items, GACHA_LINK)
