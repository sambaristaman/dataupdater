import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import news_scraper
from news_scraper import (
    nte_build_item,
    nte_discover_data_url,
    nte_parse_listing,
    nte_process,
)

FIXTURES = ROOT / "tests" / "fixtures" / "news"


def fx(name): return (FIXTURES / name).read_text(encoding="utf-8")


def test_nte_discover_data_url():
    assert nte_discover_data_url(fx("nte_main.html")) == "https://nte.perfectworld.com/include/newsData20260112.js"
    with pytest.raises(RuntimeError):
        nte_discover_data_url("<html></html>")


def test_nte_parse_listing_dedupes_and_is_english():
    items = nte_parse_listing(fx("nte_newsData.js"))
    assert len(items) == 15 and len({i["id"] for i in items}) == 15
    first = next(i for i in items if i["id"] == "264675")
    assert first["title"] == "Server Maintenance Notice: October 21"
    assert first["url"] == "https://nte.perfectworld.com/en/article/news/gamenews/20261019/264675.html"
    assert first["channel"] == "gamenews"


def test_nte_process_skips_ban_notices(monkeypatch):
    listing = nte_parse_listing(fx("nte_newsData.js"))
    assert any(news_scraper.NEWS_SKIP_TITLE_RE.search(i["title"]) for i in listing)
    monkeypatch.setattr(news_scraper, "nte_list", lambda: listing)
    discovered, to_fetch = nte_process({}, None)
    assert discovered and len(discovered) == len(to_fetch)
    skipped_ids = {i["id"] for i in listing if news_scraper.NEWS_SKIP_TITLE_RE.search(i["title"])}
    assert not any(d["key"].rsplit(":", 1)[1] in skipped_ids for d in discovered)
    assert all(d["key"].startswith("perfectworld:nte:") for d in discovered)


def test_nte_build_item():
    listing = next(i for i in nte_parse_listing(fx("nte_newsData.js")) if i["id"] == "264675")
    effective_ts = nte_process_ts(listing)
    item = nte_build_item(listing, fx("nte_article_264675.html"), effective_ts)
    assert "Hey Appraiser" in item["content"]
    assert item["category"] == "news" and item["platform"] == "perfectworld"
    assert item["game"] == "nte" and item["author"] == "Neverness to Everness"
    assert item["published"].startswith("2026-10-18T16:00:00")


def nte_process_ts(listing):
    return news_scraper.nte_effective_ts(listing["time"])


def test_nte_main_first_run_baseline(monkeypatch, tmp_path):
    state_path = tmp_path / "news_state.json"
    listing = nte_parse_listing(fx("nte_newsData.js"))
    sent = []
    monkeypatch.setenv("WEBHOOK_URL_NEWS", "https://example.invalid/webhook")
    monkeypatch.setenv("ONLY_GAME", "nte")
    monkeypatch.setenv("NEWS_STATE_PATH", str(state_path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("RUN_LAST_HOURS", raising=False)
    monkeypatch.setattr(news_scraper, "nte_list", lambda: listing)
    monkeypatch.setattr(news_scraper, "nte_fetch_article", lambda url: fx("nte_article_264675.html"))
    monkeypatch.setattr(news_scraper, "send_embeds", lambda *a, **k: sent.append(a))
    news_scraper.main()
    assert sent == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state and all(k.startswith("perfectworld:nte:") for k in state)


def test_nte_parse_listing_only_english_even_if_indentation_differs():
    js = fx("nte_newsData.js").replace("\n    \"", "\n  \"")
    items = nte_parse_listing(js)
    assert len(items) == 15 and all("/en/" in i["url"] for i in items)
