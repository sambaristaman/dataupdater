import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import gametora
from sources.common import build_bullets

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIX = Path(__file__).parent / "fixtures" / "banners"
LINK = "https://gametora.com/umamusume/gacha/history?server=en"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


FILES = {
    "en/gacha/char-standard": "gametora_char_standard.json",
    "en/gacha/support-standard": "gametora_support_standard.json",
    "en/events/champions-meeting": "gametora_champions.json",
    "en/events/legend-race": "gametora_legend.json",
    "character-cards": "gametora_character_cards_min.json",
    "support-cards": "gametora_support_cards_min.json",
}


def patch_files(monkeypatch):
    monkeypatch.setattr(gametora, "gametora_file", lambda key: load(FILES[key]))


def by_label(items, label, end=None):
    """Banners and cups repeat over the years, so optionally pin the end timestamp."""
    want = datetime.fromtimestamp(end, tz=UTC) if end else None
    return next(i for i in items if i.label == label and (want is None or i.end == want))


def test_gametora_manifest_resolves_hash(monkeypatch):
    manifest = load("gametora_manifest.json")
    urls = []

    def fake(url, params=None):
        urls.append(url)
        return manifest if "manifests" in url else {"ok": True}

    monkeypatch.setattr(gametora, "_MANIFEST", None)
    monkeypatch.setattr(gametora, "http_get_json", fake)
    assert gametora.gametora_file("en/gacha/char-standard") == {"ok": True}
    gametora.gametora_file("en/gacha/support-standard")
    h = manifest["en/gacha/char-standard"]
    assert f"https://gametora.com/data/umamusume/en/gacha/char-standard.{h}.json" in urls
    # manifest cached: fetched once
    assert sum("manifests" in u for u in urls) == 1


def test_uma_gacha_current_banner(monkeypatch):
    patch_files(monkeypatch)
    res = gametora.uma_gacha(NOW)
    assert res.source_name == "gametora.com"

    char = by_label(res.items, "Character Banner: Narita Brian", end=1792792799)
    assert char.end == datetime.fromtimestamp(1792792799, tz=UTC)
    assert char.link == LINK

    sup = by_label(res.items, "Support Banner: TM Opera O, Yamanin Zephyr", end=1792792799)
    assert sup.end == datetime.fromtimestamp(1792792799, tz=UTC)
    assert sup.link == LINK


def test_uma_gacha_unknown_pickup_falls_back_to_id(monkeypatch):
    def fake(key):
        if key == "character-cards":
            return []
        return load(FILES[key])

    monkeypatch.setattr(gametora, "gametora_file", fake)
    res = gametora.uma_gacha(NOW)
    assert any(i.label == "Character Banner: #101602" for i in res.items)


def test_uma_events_champions_and_legend(monkeypatch):
    patch_files(monkeypatch)
    res = gametora.uma_events(NOW)
    assert res.source_name == "gametora.com"

    cm = by_label(res.items, "Champions Meeting: Sagittarius Cup", end=1792187999)
    assert cm.start == datetime.fromtimestamp(1791669600, tz=UTC)
    assert cm.end == datetime.fromtimestamp(1792187999, tz=UTC)

    legend_labels = {i.label for i in res.items if i.label.startswith("Legend Race")}
    assert "Legend Race: Arima Kinen (Hard)" in legend_labels
    assert "Legend Race: Shuka Sho (Hard)" in legend_labels  # ended, returned unfiltered

    text = "\n".join(build_bullets(res.items, NOW))
    assert "Shuka Sho" not in text
    assert "Arima Kinen" in text


def test_uma_drops_items_without_any_dates(monkeypatch):
    data = {
        "en/events/champions-meeting": [{"name": "Undated Cup"}, {"name": "Dated Cup", "end": 1792187999}],
        "en/events/legend-race": [{"name_en": "Undated Race", "start": 0, "end": None}],
        "en/gacha/char-standard": [{"pickups": [[1]]}, {"pickups": [[1]], "start": 1791669600}],
        "en/gacha/support-standard": [],
        "character-cards": [{"card_id": 1, "name_en": "A"}],
        "support-cards": [],
    }
    monkeypatch.setattr(gametora, "gametora_file", lambda key: data[key])
    assert [i.label for i in gametora.uma_events(NOW).items] == ["Champions Meeting: Dated Cup"]
    gacha = gametora.uma_gacha(NOW).items
    assert len(gacha) == 1 and gacha[0].start is not None
