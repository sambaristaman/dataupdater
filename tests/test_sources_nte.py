import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import nte
from sources.common import parse_local

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIX = Path(__file__).parent / "fixtures"


def boards():
    data = json.loads((FIX / "banners" / "nte_pages_boards.json").read_text(encoding="utf-8"))
    return {
        p["title"]: p["revisions"][0]["slots"]["main"]["content"]
        for p in data["query"]["pages"]
        if p.get("revisions")
    }


def article(name):
    return (FIX / name).read_text(encoding="utf-8")


def by_label(items, label):
    return next(i for i in items if i.label == label)


def test_nte_fair_items():
    items = nte.nte_fair_items(boards())
    it = by_label(items, "Dazzling Star")
    assert it.start == parse_local("2026-10-21 11:00", 8)
    assert it.end == parse_local("2026-11-11 05:59:59", 8)
    assert it.details == "S: Akane Rin"
    assert it.link == "https://neverness-to-everness.fandom.com/wiki/Dazzling_Star/2026-10-21"
    # "Name/YYYY-MM-DD" page titles lose the date suffix; offsets like UTC+8 / GMT+8 both work
    assert by_label(items, "The Lifeline").start == parse_local("2026-07-29 11:00", 8)
    assert by_label(items, "Foretold Finale").details == "S: Blackbird"


def test_nte_fair_skips_standard():
    labels = [i.label for i in nte.nte_fair_items(boards())]
    assert "Strange Encounters" not in labels


def test_nte_parse_article_events_264675():
    items = nte.nte_parse_article_events(article("news/nte_article_264675.html"), "2026-10-19")
    labels = [i.label for i in items]
    assert labels == ["Snap! Golden Crackshot", "Puka Lucky Flip", "Gold Clash"]
    puka = by_label(items, "Puka Lucky Flip")
    assert puka.start == parse_local("2026-10-29 05:00")
    assert puka.end == parse_local("2026-11-11 05:59")
    snap = by_label(items, "Snap! Golden Crackshot")
    assert snap.start == parse_local("2026-10-21 11:00")
    assert snap.end == parse_local("2026-11-11 05:59")
    assert all('"' not in l and "Limited-Time Event" not in l for l in labels)
    # maintenance window has no quoted event name
    assert not any("Maintenance" in l or "06:00" in l for l in labels)


def test_nte_parse_article_year_rollover():
    html = (
        '<div class="articleContent">● "X" Limited-Time Event<br>'
        "Duration: December 30, 05:00 – January 5, 04:59 (server time)</div>"
    )
    (it,) = nte.nte_parse_article_events(html, "2026-12-28")
    assert it.label == "X"
    assert it.start == parse_local("2026-12-30 05:00")
    assert it.end == parse_local("2027-01-05 04:59")


def _html(duration):
    return f'<div class="articleContent">● "Gold Clash" Limited-Time Event\nDuration: {duration}</div>'


def test_nte_events_dedupe_newest_wins(monkeypatch):
    listing = [
        {"id": "1", "title": "Ver. 1.4 Patch Notes", "url": "u1", "time": "2026-10-01", "channel": "x"},
        {"id": "2", "title": "Server Maintenance Notice: Oct 15", "url": "u2", "time": "2026-10-14", "channel": "x"},
    ]
    pages = {
        "u1": _html("October 5, 05:00 – October 19, 04:59 (server time)"),
        "u2": _html("October 26, 05:00 – November 9, 04:59 (server time)"),
    }
    monkeypatch.setattr(nte, "nte_list", lambda: listing)
    monkeypatch.setattr(nte, "nte_fetch_article", lambda url: pages[url])
    res = nte.nte_events(NOW)
    assert res.source_name == "nte.perfectworld.com"
    assert len(res.items) == 1
    assert res.items[0].start == parse_local("2026-10-26 05:00")
    assert res.items[0].link == "u2"


def test_nte_events_only_relevant_titles(monkeypatch):
    listing = [
        {"id": "1", "title": "Ver. 1.4 Patch Notes", "url": "u1", "time": "2026-10-01", "channel": "x"},
        {"id": "2", "title": "Account Action Notice: 2026-10-19", "url": "u2", "time": "2026-10-19", "channel": "x"},
        {"id": "3", "title": "Server Maintenance Notice: Aug", "url": "u3", "time": "2026-08-01", "channel": "x"},
        {"id": "4", "title": "Update Notice", "url": "u4", "time": "2026-10-10", "channel": "x"},
    ]
    calls = []

    def fetch(url):
        calls.append(url)
        return "<div class='articleContent'></div>"

    monkeypatch.setattr(nte, "nte_list", lambda: listing)
    monkeypatch.setattr(nte, "nte_fetch_article", fetch)
    nte.nte_events(NOW)
    assert sorted(calls) == ["u1", "u4"]


def test_nte_gacha_uses_fandom(monkeypatch):
    monkeypatch.setattr(nte, "category_members", lambda api, cat, limit=20: list(boards()))
    monkeypatch.setattr(nte, "page_wikitexts", lambda api, titles: boards())
    res = nte.nte_gacha(NOW)
    assert res.source_name == "neverness-to-everness.fandom.com"
    assert "Dazzling Star" in [i.label for i in res.items]
