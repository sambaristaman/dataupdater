import json
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import news_scraper
from news_scraper import aniimo_build_item, aniimo_parse_list, aniimo_process

FIXTURES = ROOT / "tests" / "fixtures" / "news"


def fx(name): return (FIXTURES / name).read_text(encoding="utf-8")


def _listing(item_id):
    return next(i for i in aniimo_parse_list(json.loads(fx("aniimo_list.json"))) if i["id"] == item_id)


def test_aniimo_list_ignores_msg():
    payload = json.loads(fx("aniimo_list.json"))
    assert payload["msg"] == "Article Query Failed"
    items = aniimo_parse_list(payload)
    assert len(items) == 20 and items[0]["id"] == "100219"


def test_aniimo_empty_list_raises():
    with pytest.raises(RuntimeError):
        aniimo_parse_list({"code": 200, "msg": "ok", "data": {"list": [], "page_info": {}}})


def test_aniimo_bad_code_raises():
    with pytest.raises(RuntimeError):
        aniimo_parse_list({"code": 500, "msg": "ok", "data": {"list": [{"id": "1"}]}})


def test_aniimo_process_skips_ban_notice(monkeypatch):
    listing = aniimo_parse_list(json.loads(fx("aniimo_list.json")))
    monkeypatch.setattr(news_scraper, "aniimo_list", lambda: listing)
    discovered, to_fetch = aniimo_process({}, None)
    assert len(discovered) == 19 and len(to_fetch) == 19
    assert "aniimo:aniimo:100175" not in {d["key"] for d in discovered}
    assert "aniimo:aniimo:100219" in {d["key"] for d in discovered}


def test_aniimo_build_item_with_content():
    detail = json.loads(fx("aniimo_detail_100208.json"))["data"]
    item = aniimo_build_item(_listing("100208"), detail, 1)
    assert item["content"].strip()
    assert item["category"] == "notices"
    assert item["url"] == "https://aniimo.com/en/newslist/detail/100208"
    assert item["platform"] == "aniimo" and item["game"] == "aniimo" and item["author"] == "Aniimo"


def test_aniimo_build_item_image_only():
    detail = json.loads(fx("aniimo_detail_100219.json"))["data"]
    item = aniimo_build_item(_listing("100219"), detail, 1)
    assert item["image"] == detail["mainPic"]
    assert item["category"] == "news"
    assert BeautifulSoup(item["content"], "html.parser").get_text().strip() == ""


def test_aniimo_main_first_run_baseline(monkeypatch, tmp_path):
    state_path = tmp_path / "news_state.json"
    listing = aniimo_parse_list(json.loads(fx("aniimo_list.json")))
    detail = json.loads(fx("aniimo_detail_100208.json"))["data"]
    sent = []
    monkeypatch.setenv("WEBHOOK_URL_NEWS", "https://example.invalid/webhook")
    monkeypatch.setenv("ONLY_GAME", "aniimo")
    monkeypatch.setenv("NEWS_STATE_PATH", str(state_path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("RUN_LAST_HOURS", raising=False)
    monkeypatch.setattr(news_scraper, "aniimo_list", lambda: listing)
    monkeypatch.setattr(news_scraper, "aniimo_detail", lambda item_id: detail)
    monkeypatch.setattr(news_scraper, "send_embeds", lambda *a, **k: sent.append(a))
    news_scraper.main()
    assert sent == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state and all(k.startswith("aniimo:aniimo:") for k in state)
