import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import news_scraper


def _seed_state(path: Path, game: str) -> None:
    path.write_text(
        json.dumps({f"seed:{game}:0": {"last_modified": 0, "last_sent_hash": ""}}),
        encoding="utf-8",
    )


def _run_live(monkeypatch, tmp_path, game: str) -> None:
    state_path = tmp_path / "news_state.json"
    _seed_state(state_path, game)

    sent = []

    def _mock_send(webhook_url, embeds):
        sent.extend(embeds)

    monkeypatch.setenv("WEBHOOK_URL_NEWS", "https://example.invalid/webhook")
    monkeypatch.setenv("ONLY_GAME", game)
    monkeypatch.setenv("NEWS_STATE_PATH", str(state_path))
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setattr(news_scraper, "send_embeds", _mock_send)

    news_scraper.main()

    assert state_path.exists()
    json.loads(state_path.read_text(encoding="utf-8"))


def test_news_scraper_live_genshin(monkeypatch, tmp_path):
    _run_live(monkeypatch, tmp_path, "genshin")


def test_news_scraper_live_starrail(monkeypatch, tmp_path):
    _run_live(monkeypatch, tmp_path, "starrail")


def test_news_scraper_live_honkai3rd(monkeypatch, tmp_path):
    _run_live(monkeypatch, tmp_path, "honkai3rd")


def test_news_scraper_live_zzz(monkeypatch, tmp_path):
    _run_live(monkeypatch, tmp_path, "zzz")


def test_news_scraper_live_endfield(monkeypatch, tmp_path):
    _run_live(monkeypatch, tmp_path, "endfield")


def test_news_scraper_live_shadowverse(monkeypatch, tmp_path):
    _run_live(monkeypatch, tmp_path, "shadowverse")
