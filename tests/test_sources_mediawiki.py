import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import mediawiki
from sources.mediawiki import category_members, page_wikitexts, parse_template

FIX = Path(__file__).parent / "fixtures" / "banners"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def wikitext(pages_fixture, title):
    for p in load(pages_fixture)["query"]["pages"]:
        if p["title"] == title:
            return p["revisions"][0]["slots"]["main"]["content"]
    raise KeyError(title)


def test_parse_template_convene():
    text = wikitext("wuwa_pages_resonator.json", "Horizon of Dawnbreak/2026-09-30")
    main = parse_template(text, "Convene")
    assert main["type"] == "Featured Resonator"
    assert main["time_start"] == "2026-09-30 11:00"
    assert main["link"].endswith("/news/detail/5546")
    pool = parse_template(text, "Convene/Pool")
    assert pool["resonator_5_F"] == "Chisa"
    assert pool["resonator_4_F"] == "Buling;Taoqi;Youhu"


def test_parse_template_nested_braces():
    t = parse_template("{{Event|name = A {{sic}} B|time_end = 2026-10-01}}", "event")
    assert t["name"] == "A {{sic}} B"
    assert t["time_end"] == "2026-10-01"


def test_parse_template_missing_returns_none():
    assert parse_template("{{Other|a = 1}}", "Event") is None
    assert parse_template("no templates", "Event") is None


def test_page_wikitexts_batches(monkeypatch):
    calls = []

    def fake(url, params=None):
        calls.append((url, params))
        titles = params["titles"].split("|")
        return {"query": {"pages": [
            {"title": t, "revisions": [{"slots": {"main": {"content": "x " + t}}}]} for t in titles
        ]}}

    monkeypatch.setattr(mediawiki, "http_get_json", fake)
    titles = [f"P{i}" for i in range(120)]
    out = page_wikitexts("https://x/api.php", titles)
    assert len(calls) == 3
    assert [len(c[1]["titles"].split("|")) for c in calls] == [50, 50, 20]
    assert calls[0][1]["rvslots"] == "main" and calls[0][1]["formatversion"] == 2
    assert out["P119"] == "x P119" and len(out) == 120


def test_category_members(monkeypatch):
    seen = {}

    def fake(url, params=None):
        seen.update(params)
        return load("wuwa_cat_events.json")

    monkeypatch.setattr(mediawiki, "http_get_json", fake)
    titles = category_members("https://x/api.php", "Category:Events")
    assert "Artisan's Search" in titles and len(titles) == 20
    assert seen["cmsort"] == "timestamp" and seen["cmdir"] == "desc" and seen["cmnamespace"] == 0
