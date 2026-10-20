# -*- coding: utf-8 -*-
"""Arknights: Endfield events and headhunting banners from the wiki.gg Cargo tables."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sources.common import Item, SourceResult, http_get_json, parse_local

API = "https://endfield.wiki.gg/api.php"
WIKI = "https://endfield.wiki.gg/wiki/"
SOURCE_NAME = "endfield.wiki.gg"
OFFSET = 8
LIMIT = 30


def cargo_query(table: str, fields: List[str], where: str, order_by: str) -> List[dict]:
    """Rows (the `title` objects) of a Cargo query."""
    data = http_get_json(
        API,
        {
            "action": "cargoquery",
            "tables": table,
            "fields": ",".join(fields),
            "where": where,
            "limit": LIMIT,
            "order_by": order_by,
            "format": "json",
        },
    )
    return [row["title"] for row in data.get("cargoquery", [])]


def _result(items: List[Item]) -> SourceResult:
    return SourceResult(items=items, source_name=SOURCE_NAME, source_url=WIKI)


def _where(now: datetime) -> str:
    local = (now.astimezone(timezone.utc) + timedelta(hours=OFFSET)).strftime("%Y-%m-%d %H:%M:%S")
    return f"AsiaEndTime >= '{local}' OR AsiaEndTime IS NULL"


def _date(value: Optional[str]) -> Optional[datetime]:
    try:
        return parse_local((value or "").strip(), OFFSET)
    except ValueError:
        return None


def _link(name: str) -> str:
    return WIKI + name.strip().replace(" ", "_")


def _names(value: Optional[str]) -> str:
    return ", ".join(v.strip() for v in (value or "").split(";") if v.strip())


def _query(table: str, fields: List[str], now: datetime) -> List[Dict[str, str]]:
    return cargo_query(
        table, fields + ["AsiaStartTime", "AsiaEndTime"], _where(now), "AsiaStartTime DESC"
    )


def _details(rate_up: Optional[str], others: Optional[str]) -> Optional[str]:
    rate_up = (rate_up or "").strip()
    if rate_up:
        return f"Rate-up: {rate_up}"
    others = _names(others)
    return f"Limited: {others}" if others else None


def endfield_events(now: datetime) -> SourceResult:
    items: List[Item] = []
    for row in _query("Events", ["Event", "Type"], now):
        name = (row.get("Event") or "").strip()
        if not name:
            continue
        start, end = _date(row.get("AsiaStartTime")), _date(row.get("AsiaEndTime"))
        if start is None and end is None:
            continue
        items.append(Item(label=name, start=start, end=end, link=_link(name)))
    return _result(items)


def endfield_gacha(now: datetime) -> SourceResult:
    items: List[Item] = []

    for row in _query("Banners", ["Banner", "RateUpOperator", "LimitedOperators"], now):
        name = (row.get("Banner") or "").strip()
        start, end = _date(row.get("AsiaStartTime")), _date(row.get("AsiaEndTime"))
        if not name or (start is None and end is None):
            continue
        details = _details(row.get("RateUpOperator"), row.get("LimitedOperators"))
        items.append(Item(label=name, start=start, end=end, link=_link(name), details=details))

    for row in _query("Issues", ["Issue", "RateUpWeapon", "Weapons"], now):
        name = (row.get("Issue") or "").strip()
        start, end = _date(row.get("AsiaStartTime")), _date(row.get("AsiaEndTime"))
        if not name or (start is None and end is None):
            continue
        details = _details(row.get("RateUpWeapon"), row.get("Weapons"))
        items.append(
            Item(label=f"Arsenal: {name}", start=start, end=end, link=_link(name), details=details)
        )

    return _result(items)
