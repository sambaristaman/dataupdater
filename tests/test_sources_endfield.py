import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import endfield
from sources.common import parse_local

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIX = Path(__file__).parent / "fixtures" / "banners"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fake_http(calls):
    tables = {
        "Banners": "endfield_banners.json",
        "Issues": "endfield_issues.json",
        "Events": "endfield_events.json",
    }

    def fake(url, params=None, headers=None):
        calls.append((url, params, headers))
        return load(tables[params["tables"]])

    return fake


def by_label(items, label):
    return next(i for i in items if i.label == label)


def test_endfield_gacha_operators_and_weapons(monkeypatch):
    monkeypatch.setattr(endfield, "http_get_json", fake_http([]))
    res = endfield.endfield_gacha(NOW)
    assert res.source_name == "endfield.wiki.gg"

    hearth = by_label(res.items, "The Hearth Fire Beckons the Starry Flow")
    assert hearth.start == parse_local("2026-11-05 04:00:00")
    assert hearth.end == parse_local("2026-11-26 03:59:59")
    assert hearth.details == "Rate-up: Argent Flow"
    assert hearth.link == "https://endfield.wiki.gg/wiki/The_Hearth_Fire_Beckons_the_Starry_Flow"

    moon = by_label(res.items, "Arsenal: Moongleam")
    assert moon.details == "Rate-up: Lunar Calling"

    spring = by_label(res.items, "Ancestral Spring Flows Anew")
    assert spring.details == "Limited: Tangtang"


def test_endfield_events_blank_end_kept_as_start_only(monkeypatch):
    monkeypatch.setattr(endfield, "http_get_json", fake_http([]))
    res = endfield.endfield_events(NOW)
    ev = by_label(res.items, "Contingency Contract: Indigo Experimental Operation")
    assert ev.start == parse_local("2026-11-05 04:00:00")
    assert ev.end is None


def test_endfield_where_clause(monkeypatch):
    calls = []
    monkeypatch.setattr(endfield, "http_get_json", fake_http(calls))
    endfield.endfield_events(NOW)
    endfield.endfield_gacha(NOW)
    assert calls
    for url, params, headers in calls:
        assert url == "https://endfield.wiki.gg/api.php"
        assert params["where"] == "AsiaEndTime >= '2026-10-20 20:00:00' OR AsiaEndTime IS NULL"


def test_endfield_sends_descriptive_bot_user_agent(monkeypatch):
    calls = []
    monkeypatch.setattr(endfield, "http_get_json", fake_http(calls))
    endfield.endfield_events(NOW)
    endfield.endfield_gacha(NOW)
    assert calls
    for _, _, headers in calls:
        ua = headers["User-Agent"]
        assert ua == endfield.BOT_USER_AGENT
        assert ua.startswith("dataupdater-bot/") and "Mozilla" not in ua and "@" not in ua


def test_other_sources_keep_default_user_agent(monkeypatch):
    """Only Endfield overrides the UA; other sources go through the shared session as-is."""
    from sources import common, hoyo

    seen = []

    class Rec:
        def get(self, url, **kw):
            seen.append(kw.get("headers"))
            raise RuntimeError("stop")

    monkeypatch.setattr(common, "SESSION", Rec())
    try:
        hoyo.genshin_gacha(NOW)
    except RuntimeError:
        pass
    assert seen == [None]
    assert "Chrome" in common.USER_AGENT


def test_endfield_gacha_skips_permanent_blank_end_rows(monkeypatch):
    monkeypatch.setattr(endfield, "http_get_json", fake_http([]))
    labels = [i.label for i in endfield.endfield_gacha(NOW).items]
    for permanent in (
        "Basic Headhunting", "New Horizons Headhunting", "Arsenal: Solid Ice",
        "Arsenal: Cosmic Voice", "Arsenal: Far Expedition", "Arsenal: Rising Mount",
        "Arsenal: Thunderous Peal",
    ):
        assert permanent not in labels
    assert "The Hearth Fire Beckons the Starry Flow" in labels


def test_endfield_gacha_keeps_recent_blank_end_row(monkeypatch):
    rows = {"cargoquery": [{"title": {
        "Banner": "Fresh", "RateUpOperator": "X", "LimitedOperators": "",
        "AsiaStartTime": "2026-10-10 04:00:00", "AsiaEndTime": "",
    }}]}
    monkeypatch.setattr(
        endfield, "http_get_json",
        lambda url, params=None, headers=None: rows if params["tables"] == "Banners" else {"cargoquery": []},
    )
    items = endfield.endfield_gacha(NOW).items
    assert [(i.label, i.end) for i in items] == [("Fresh", None)]


def test_endfield_gacha_fits_one_message(monkeypatch):
    import scraper
    from sources.common import build_bullets

    monkeypatch.setattr(endfield, "http_get_json", fake_http([]))
    res = endfield.endfield_gacha(NOW)
    msgs = scraper.build_messages("Arknights: Endfield — Events & Banners — Gacha",
                                  res.source_url, res.source_name, build_bullets(res.items, NOW))
    assert len(msgs) == 1
