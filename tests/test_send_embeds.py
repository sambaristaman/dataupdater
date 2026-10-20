import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import news_scraper


class _Resp:
    status_code = 200
    text = ""


def _capture(monkeypatch):
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json["embeds"])
        return _Resp()

    monkeypatch.setattr(news_scraper.SESSION, "post", fake_post)
    monkeypatch.setattr(news_scraper.time, "sleep", lambda s: None)
    monkeypatch.setattr(news_scraper, "DRY_RUN", False)
    return sent


def test_send_embeds_splits_messages_over_6000_chars(monkeypatch):
    sent = _capture(monkeypatch)
    embeds = [
        {"title": "t", "description": "a" * 4088},
        {"description": "b" * 3598, "footer": {"text": "news · nte"}},
    ]
    news_scraper.send_embeds("https://example.invalid/hook", embeds)
    assert len(sent) == 2
    for batch in sent:
        assert sum(len(e.get("title", "")) + len(e.get("description", "")) for e in batch) <= 6000


def test_send_embeds_keeps_small_embeds_in_one_message(monkeypatch):
    sent = _capture(monkeypatch)
    news_scraper.send_embeds("https://example.invalid/hook", [{"description": "x" * 100} for _ in range(3)])
    assert len(sent) == 1 and len(sent[0]) == 3
