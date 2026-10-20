import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import wuwa
from sources.common import parse_local

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIX = Path(__file__).parent / "fixtures" / "banners"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def pages(name):
    return {
        p["title"]: p["revisions"][0]["slots"]["main"]["content"]
        for p in load(name)["query"]["pages"]
        if p.get("revisions")
    }


def patch(monkeypatch, cats):
    """cats: {category: (cat_fixture, pages_fixture)}"""
    all_pages = {}
    for _, pf in cats.values():
        all_pages.update(pages(pf))

    def fake_members(api, category, limit=20):
        cf = cats[category][0]
        return [m["title"] for m in load(cf)["query"]["categorymembers"]]

    monkeypatch.setattr(wuwa, "category_members", fake_members)
    monkeypatch.setattr(wuwa, "page_wikitexts", lambda api, titles: {t: all_pages[t] for t in titles})


def by_label(items, label):
    return next(i for i in items if i.label == label)


def test_wuwa_gacha_items(monkeypatch):
    patch(monkeypatch, {
        "Category:Featured_Resonator_Convenes": ("wuwa_cat_resonator.json", "wuwa_pages_resonator.json"),
        "Category:Featured_Weapon_Convenes": ("wuwa_cat_weapon.json", "wuwa_pages_weapon.json"),
    })
    res = wuwa.wuwa_gacha(NOW)
    assert res.source_name == "wutheringwaves.fandom.com"
    # label drops the /YYYY-MM-DD suffix; several pages may share it, pick the 2026-09-30 one
    matches = [
        i for i in res.items
        if i.label == "Horizon of Dawnbreak" and i.start == parse_local("2026-09-30 11:00")
    ]
    assert matches, "2026-09-30 convene missing"
    it = matches[0]
    assert it.link == "https://wutheringwaves.kurogames.com/en/main/news/detail/5546"
    assert it.details == "5★ Chisa · 4★ Buling, Taoqi, Youhu"
    assert it.end == parse_local("2026-10-22 09:59")
    weapon = by_label(res.items, "Absolute Pulsation: Kumokiri")
    assert weapon.details.startswith("5★ Kumokiri · 4★ ")


def test_wuwa_events_skip_blank_dates(monkeypatch):
    patch(monkeypatch, {"Category:Events": ("wuwa_cat_events.json", "wuwa_pages_events.json")})
    res = wuwa.wuwa_events(NOW)
    labels = [i.label for i in res.items]
    assert "Cubie War" not in labels  # {{Event}} with blank dates
    assert "Artisan's Search" in labels
    it = by_label(res.items, "Artisan's Search")
    assert it.start == parse_local("2026-10-08 04:00", 8)
    assert it.link == "https://x.com/Wuthering_Waves/status/2107652131511509056"  # from |link
    # no |link: falls back to the wiki page URL
    assert by_label(res.items, "Shape of Yesterday").link == (
        "https://wutheringwaves.fandom.com/wiki/Shape_of_Yesterday"
    )
    # blank |name falls back to the page title
    assert "If Dreams Still Reverberate" in labels
    # "none"/"no" end markers mean open-ended, not a crash
    assert by_label(res.items, "Shape of Yesterday").end is None
    # explicit UTC-7 offset is honoured
    battle = by_label(res.items, "Wuthering Waves Battle Rush 2026-07-10")
    assert battle.start == parse_local("2026-07-10", -7)


def test_wuwa_events_ignores_pages_without_event(monkeypatch):
    monkeypatch.setattr(wuwa, "category_members", lambda api, category, limit=20: ["A", "B"])
    monkeypatch.setattr(
        wuwa,
        "page_wikitexts",
        lambda api, titles: {
            "A": "plain text",
            "B": "{{Event|name=B|time_start=2026-10-01 04:00|time_end=2026-10-30}}",
        },
    )
    res = wuwa.wuwa_events(NOW)
    assert [i.label for i in res.items] == ["B"]
