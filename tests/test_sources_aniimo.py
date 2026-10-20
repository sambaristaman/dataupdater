# -*- coding: utf-8 -*-
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import aniimo
from sources.common import parse_local

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIX = Path(__file__).parent / "fixtures"


def content(item_id):
    data = json.loads((FIX / "banners" / f"aniimo_detail_{item_id}.json").read_text(encoding="utf-8"))
    return data["data"]["content"]


def by_label(items):
    return {i.label: i for i in items}


def test_aniimo_parse_events_100145():
    items = by_label(aniimo.aniimo_parse_events(content(100145)))
    g = items["Glamour Star"]
    assert g.start == parse_local("2026/09/25 04:00", -4)
    assert g.end == parse_local("2026/10/02 03:59", -4)
    assert items["Journey Chronicles"].end == parse_local("2026/10/29 03:59", -4)


def test_aniimo_skips_recurring_and_unlock_only():
    labels = {i.label for i in aniimo.aniimo_parse_events(content(100145))}
    assert "Holo-Battle Interlink" not in labels
    assert "Operation: Egg Heist Team Mode—Chaos Mode" not in labels


def test_aniimo_subtitle_line_before_region():
    items = by_label(aniimo.aniimo_parse_events(content(100145)))
    assert "Vein Abundance" not in items
    v = items["Vein Abundance: Rosetower Woods"]
    assert v.start == parse_local("2026/09/28 04:00", -4)


def test_aniimo_europe_only_uses_utc():
    html = "<p>Foo</p><p>[Event Period]</p><p>「Aniimo-Europe」2026/09/28 04:00 - 2026/10/05 03:59 UTC</p>"
    (it,) = aniimo.aniimo_parse_events(html)
    assert it.label == "Foo"
    assert it.start == parse_local("2026/09/28 04:00", 0)
    assert it.end == parse_local("2026/10/05 03:59", 0)


def test_aniimo_bad_block_does_not_discard_others():
    html = (
        "<p>Bad</p><p>[Event Period]</p><p>「Aniimo-Europe」2026/13/45 04:00 - 2026/10/05 03:59 UTC</p>"
        "<p>Good</p><p>[Event Period]</p><p>「Aniimo-Europe」2026/09/28 04:00 - 2026/10/05 03:59 UTC</p>"
    )
    assert [i.label for i in aniimo.aniimo_parse_events(html)] == ["Good"]


def test_aniimo_uses_opening_time_blocks():
    assert aniimo.aniimo_parse_events(content(100198)) == []


def test_aniimo_events_filters_update_notices(monkeypatch):
    listing = json.loads((FIX / "news" / "aniimo_list.json").read_text(encoding="utf-8"))["data"]["list"]
    fetched = []

    def fake_detail(item_id):
        fetched.append(str(item_id))
        return {"content": content(100145)}

    monkeypatch.setattr(aniimo, "aniimo_list", lambda: listing)
    monkeypatch.setattr(aniimo, "aniimo_detail", fake_detail)
    res = aniimo.aniimo_events(NOW)
    notices = sorted(
        (a for a in listing if "Update Notice" in a["title"]), key=lambda a: a["publishTime"], reverse=True
    )
    expected = [str(a["id"]) for a in notices][:6]
    assert fetched == expected and fetched
    assert res.source_name == "aniimo.com"
    g = by_label(res.items)["Glamour Star"]
    assert g.link == f"https://aniimo.com/en/newslist/detail/{expected[0]}"
