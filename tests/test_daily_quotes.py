import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import daily_quotes


def test_load_quotes_missing_env(monkeypatch):
    monkeypatch.delenv("DAILY_QUOTES_YAML", raising=False)
    monkeypatch.delenv("MENTION_QUOTES_YAML", raising=False)
    with pytest.raises(ValueError, match="Missing DAILY_QUOTES_YAML"):
        daily_quotes.load_quotes_from_env()


def test_load_quotes_invalid_yaml(monkeypatch):
    monkeypatch.setenv("DAILY_QUOTES_YAML", "[")
    with pytest.raises(ValueError, match="Invalid DAILY_QUOTES_YAML"):
        daily_quotes.load_quotes_from_env()


def test_load_quotes_only_regular(monkeypatch):
    monkeypatch.setenv("DAILY_QUOTES_YAML", '- "Hello world"')
    monkeypatch.delenv("MENTION_QUOTES_YAML", raising=False)
    quotes, mention_quotes = daily_quotes.load_quotes_from_env()
    assert quotes == ["Hello world"]
    assert mention_quotes == []


def test_load_quotes_with_mention(monkeypatch):
    monkeypatch.setenv("DAILY_QUOTES_YAML", '- "Hello"')
    monkeypatch.setenv("MENTION_QUOTES_YAML", '- "Hi {user}!"')
    quotes, mention_quotes = daily_quotes.load_quotes_from_env()
    assert quotes == ["Hello"]
    assert mention_quotes == ["Hi {user}!"]


def test_load_mention_invalid_yaml(monkeypatch):
    monkeypatch.setenv("DAILY_QUOTES_YAML", '- "Hello"')
    monkeypatch.setenv("MENTION_QUOTES_YAML", "[")
    with pytest.raises(ValueError, match="Invalid MENTION_QUOTES_YAML"):
        daily_quotes.load_quotes_from_env()


def test_build_message_regular(monkeypatch):
    monkeypatch.setattr(daily_quotes.random, "random", lambda: 0.9)
    quotes = ["Regular only"]
    mention_quotes = ["Hi {user}"]
    message, used_mention = daily_quotes.build_message(
        quotes, mention_quotes, "123", "token"
    )
    assert message == "Regular only"
    assert used_mention is False


def test_build_message_mention_replaces(monkeypatch):
    monkeypatch.setattr(daily_quotes.random, "random", lambda: 0.01)
    monkeypatch.setattr(daily_quotes, "get_channel_guild_id", lambda *_: "guild1")
    monkeypatch.setattr(
        daily_quotes,
        "fetch_guild_members",
        lambda *_: [{"user": {"id": "42", "bot": False}}],
    )
    quotes = ["Regular only"]
    mention_quotes = ["Hello {user}!"]
    message, used_mention = daily_quotes.build_message(
        quotes, mention_quotes, "123", "token"
    )
    assert message == "Hello <@42>!"
    assert used_mention is True


def test_build_message_mention_fallback_no_members(monkeypatch):
    monkeypatch.setattr(daily_quotes.random, "random", lambda: 0.01)
    monkeypatch.setattr(daily_quotes, "get_channel_guild_id", lambda *_: "guild1")
    monkeypatch.setattr(daily_quotes, "fetch_guild_members", lambda *_: [])
    quotes = ["Regular only"]
    mention_quotes = ["Hello {user}!"]
    message, used_mention = daily_quotes.build_message(
        quotes, mention_quotes, "123", "token"
    )
    assert message == "Regular only"
    assert used_mention is False
