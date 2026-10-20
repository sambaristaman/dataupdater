import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scraper
from sources.common import (
    Item,
    SourceResult,
    build_bullets,
    format_bullet,
    parse_local,
    parse_offset,
    with_fallback,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_parse_local_formats():
    assert parse_local("2026-09-30 11:00", 8) == datetime(2026, 9, 30, 3, tzinfo=UTC)
    assert parse_local("2026/10/02 03:59", -4) == datetime(2026, 10, 2, 7, 59, tzinfo=UTC)
    assert parse_local("2026-10-22", 8) == datetime(2026, 10, 21, 16, tzinfo=UTC)
    assert parse_local("  ") is None


def test_parse_offset():
    assert parse_offset("GMT+8") == 8 and parse_offset("UTC-4") == -4
    assert parse_offset("UTC") == 0 and parse_offset("") == 8


def test_format_bullet_full():
    it = Item("Horizon of Dawnbreak", datetime(2026, 9, 30, 3, tzinfo=UTC),
              datetime(2026, 10, 22, 1, 59, tzinfo=UTC), "https://x/y", "Chisa · Buling, Taoqi")
    assert format_bullet(it) == "• [Horizon of Dawnbreak](https://x/y) — <t:1790737200:d> → <t:1792634340:d> (ends <t:1792634340:R>) · Chisa · Buling, Taoqi"


def test_format_bullet_start_only():
    it = Item("Cubie War", datetime(2026, 10, 15, tzinfo=UTC), None)
    assert format_bullet(it) == "• Cubie War — from <t:1792022400:d>"


def test_format_bullet_sanitizes_label():
    it = Item("Snap! [Golden] — Crackshot", None, datetime(2026, 11, 1, tzinfo=UTC))
    line = format_bullet(it)
    assert line.startswith("• Snap! (Golden) - Crackshot — until <t:")
    assert scraper.parse_bullet(line)[0] == "Snap! (Golden) - Crackshot"


def test_build_bullets_sections_and_filters():
    cur = Item("Cur", NOW - timedelta(days=1), NOW + timedelta(days=5))
    soon = Item("Soon", NOW + timedelta(days=3), NOW + timedelta(days=20))
    far = Item("Far", NOW + timedelta(days=40), NOW + timedelta(days=60))
    done = Item("Done", NOW - timedelta(days=9), NOW - timedelta(days=1))
    lines = build_bullets([soon, done, far, cur], NOW)
    assert lines[0] == "__Current__" and lines[1].startswith("• Cur")
    assert lines[2] == "__Upcoming__" and lines[3].startswith("• Soon")
    assert len(lines) == 4


def test_build_bullets_sorted_by_end_and_deduped():
    a = Item("A", NOW - timedelta(days=1), NOW + timedelta(days=9))
    b = Item("B", NOW - timedelta(days=1), NOW + timedelta(days=2))
    lines = build_bullets([a, b, Item("a", a.start, a.end)], NOW)
    assert [l[2] for l in lines[1:]] == ["B", "A"]


def test_with_fallback_empty_primary_uses_fallback():
    p = lambda now: SourceResult([], "p", "u1")
    f = lambda now: SourceResult([Item("X", None, NOW)], "f", "u2")
    assert with_fallback(p, f)(NOW).source_name == "f"


def test_with_fallback_primary_raises_uses_fallback():
    def p(now):
        raise RuntimeError("boom")
    f = lambda now: SourceResult([Item("X", None, NOW)], "f", "u2")
    assert with_fallback(p, f)(NOW).source_name == "f"
