import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources import hoyo
from sources.common import SourceResult, parse_local
from sources.hoyo import ennead_banners, ennead_events, official_items

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
FIX = Path(__file__).parent / "fixtures" / "banners"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def ts(n):
    return datetime.fromtimestamp(n, UTC)


def by_label(items, label):
    return next(i for i in items if i.label == label)


def test_ennead_genshin_events():
    items = ennead_events(load("ennead_genshin.json"))
    it = by_label(items, "Predictive Victory Dynamics")
    assert it.start == ts(1791770400) and it.end == ts(1792720799)
    # events without dates (start/end == 0) are dropped
    assert not any(i.label == "Carefree Snowball Fight" for i in items)


def test_ennead_genshin_banners():
    items = ennead_banners(load("ennead_genshin.json"), "Event Wish")
    first = items[0]
    assert first.label == "Character Event Wish: Skirk"
    assert first.details.startswith("5★ Skirk · 4★ Dahlia, Candace")
    assert first.start == ts(1791885600) and first.end == ts(1793689199)
    labels = [i.label for i in items]
    assert "Character Event Wish 2: Escoffier" in labels
    assert "Weapon Event Wish: Azurelight, Symphonist of Scents" in labels
    assert len(set(labels)) == len(labels)
    weapon = by_label(items, "Weapon Event Wish: Azurelight, Symphonist of Scents")
    assert weapon.details.startswith("5★ Azurelight, Symphonist of Scents · 4★ Sturdy Bone")


def test_ennead_hsr_banner_empty_name():
    items = ennead_banners(load("ennead_starrail.json"), "Event Warp")
    ev = by_label(items, "Event Warp: Evanescia")
    assert ev.details == "5★ Evanescia · 4★ Qingque, Xueyi, Misha"
    assert ev.start == ts(1790560800) and ev.end == ts(1792580340)
    lc = by_label(items, "Light Cone Warp: Until the Flowers Bloom Again")
    assert lc.details.startswith("5★ Until the Flowers Bloom Again · 4★ Post-Op Conversation")
    labels = [i.label for i in items]
    assert len(set(labels)) == len(labels) == 6


def test_official_items_genshin_timezone():
    items = official_items(load("hoyo_ann_genshin.json"))
    it = by_label(items, "New Content Overview")
    assert it.start == parse_local("2026-09-22 18:00:00", -5)
    assert it.end == parse_local("2026-11-03 17:00:00", -5)
    assert it.link is None


def test_official_items_hsr_reads_pic_list():
    items = official_items(load("hoyo_ann_starrail.json"))
    assert any("Event Warp" in i.label for i in items)
    assert not any(i.label == "" for i in items)


def test_official_items_strips_html():
    payload = {"retcode": 0, "data": {"timezone": 8, "list": [{"list": [
        {"title": "<p>Hello<br>World</p>", "start_time": "2026-10-01 00:00:00",
         "end_time": "2026-10-30 00:00:00"}]}]}}
    items = official_items(payload)
    assert items[0].label == "Hello World"
    assert items[0].end == parse_local("2026-10-30 00:00:00", 8)


def test_wrappers_use_ennead_and_fallback(monkeypatch):
    def fake(url, params=None):
        if "ennead" in url:
            return load("ennead_genshin.json")
        return load("hoyo_ann_genshin.json")

    monkeypatch.setattr(hoyo, "http_get_json", fake)
    res = hoyo.genshin_events(NOW)
    assert isinstance(res, SourceResult) and res.source_name == "ennead.cc"
    assert res.source_url == hoyo.ENNEAD_GENSHIN
    gacha = hoyo.genshin_gacha(NOW)
    assert gacha.items[0].label == "Character Event Wish: Skirk"

    def failing(url, params=None):
        if "ennead" in url:
            raise RuntimeError("down")
        return load("hoyo_ann_starrail.json")

    monkeypatch.setattr(hoyo, "http_get_json", failing)
    res = hoyo.hsr_events(NOW)
    assert res.source_name == "hoyoverse announcements"
    assert res.source_url == hoyo.SR_ANN
    assert res.items
