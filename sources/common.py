# -*- coding: utf-8 -*-
"""Common model, bullet formatting and HTTP helpers for per-game sources."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 30

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


@dataclass
class Item:
    label: str
    start: Optional[datetime]
    end: Optional[datetime]
    link: Optional[str] = None
    details: Optional[str] = None


@dataclass
class SourceResult:
    items: List[Item]
    source_name: str
    source_url: str


Source = Callable[[datetime], SourceResult]


# --- Parsing helpers ---

_LOCAL_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def parse_local(value: str, offset_hours: float = 8) -> Optional[datetime]:
    """Parse a local timestamp in the given UTC offset into an aware UTC datetime."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _LOCAL_FORMATS:
        try:
            naive = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return (naive - timedelta(hours=offset_hours)).replace(tzinfo=timezone.utc)
    raise ValueError(f"Unrecognized date format: {value!r}")


_OFFSET_RE = re.compile(r"^(?:GMT|UTC)\s*(?:([+-])\s*(\d+(?:\.\d+)?))?$", re.I)


def parse_offset(value: str, default: float = 8) -> float:
    """'GMT+8' -> 8, 'UTC-4' -> -4, 'UTC' -> 0, '' -> default."""
    m = _OFFSET_RE.match((value or "").strip())
    if not m:
        return default
    if not m.group(1):
        return 0
    num = float(m.group(2))
    num = int(num) if num == int(num) else num
    return -num if m.group(1) == "-" else num


# --- Formatting ---

def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def format_bullet(item: Item) -> str:
    label = item.label.replace("—", "-").replace("[", "(").replace("]", ")")
    head = f"[{label}]({item.link})" if item.link else label
    line = f"• {head}"
    if item.start and item.end:
        s, e = _ts(item.start), _ts(item.end)
        line += f" — <t:{s}:d> → <t:{e}:d> (ends <t:{e}:R>)"
    elif item.end:
        e = _ts(item.end)
        line += f" — until <t:{e}:d> (ends <t:{e}:R>)"
    elif item.start:
        line += f" — from <t:{_ts(item.start)}:d>"
    if item.details:
        line += f" · {item.details}"
    return line


def build_bullets(items: List[Item], now: datetime, upcoming_days: int = 21) -> List[str]:
    horizon = now + timedelta(days=upcoming_days)
    seen = set()
    current: List[Item] = []
    upcoming: List[Item] = []
    for it in items:
        if it.end is not None and it.end < now:
            continue
        key = it.label.lower()
        if key in seen:
            continue
        if it.start is None or it.start <= now:
            current.append(it)
        elif it.start <= horizon:
            upcoming.append(it)
        else:
            continue
        seen.add(key)

    last = datetime.max.replace(tzinfo=timezone.utc)
    current.sort(key=lambda i: i.end or last)
    upcoming.sort(key=lambda i: i.start)

    lines: List[str] = []
    if current:
        lines.append("__Current__")
        lines.extend(format_bullet(i) for i in current)
    if upcoming:
        lines.append("__Upcoming__")
        lines.extend(format_bullet(i) for i in upcoming)
    return lines


# --- Fallback ---

def with_fallback(primary: Source, fallback: Source) -> Source:
    def run(now: datetime) -> SourceResult:
        try:
            result = primary(now)
            if result.items:
                return result
            print("[WARN] primary source returned no items; using fallback")
        except Exception as e:
            print(f"[WARN] primary source failed ({e}); using fallback")
        return fallback(now)

    return run


# --- HTTP ---

def http_get_json(url: str, params: Optional[dict] = None) -> Any:
    r = SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def http_get_text(url: str, params: Optional[dict] = None) -> str:
    r = SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text
