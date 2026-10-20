import json
from datetime import datetime, timedelta, timezone

import pytest

import scraper
from sources.common import Item, SourceResult, format_bullet

UTC = timezone.utc
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
GI_URL = "https://api.ennead.cc/mihoyo/genshin/calendar"

ITEM = Item("Moontrace", NOW - timedelta(days=2), NOW + timedelta(days=5), "https://x/moon")


def ok_source(now):
    return SourceResult([ITEM], "ennead.cc", GI_URL)


def empty_source(now):
    return SourceResult([], "ennead.cc", GI_URL)


def boom_source(now):
    raise RuntimeError("boom")


@pytest.fixture
def calls(monkeypatch, tmp_path):
    rec = {"post": [], "edit": [], "delete": [], "embed": []}
    counter = iter(range(1000, 9999))

    def post(url, content):
        rec["post"].append((url, content))
        return str(next(counter))

    def edit(url, mid, content):
        rec["edit"].append((url, mid, content))
        return True

    def delete(url, mid):
        rec["delete"].append((url, mid))

    def embed(url, emb, content=None):
        rec["embed"].append((url, emb, content))

    monkeypatch.setattr(scraper, "DRY_RUN", False)
    monkeypatch.setattr(scraper, "FORCE_NEW", False)
    monkeypatch.setattr(scraper, "ONLY_KEY", "")
    monkeypatch.setattr(scraper, "DISABLE_UMA_EVENTS", False)
    monkeypatch.setattr(scraper, "webhook_post", post)
    monkeypatch.setattr(scraper, "webhook_edit", edit)
    monkeypatch.setattr(scraper, "webhook_delete", delete)
    monkeypatch.setattr(scraper, "discord_webhook_post_embed", embed)
    monkeypatch.setattr(scraper, "MESSAGE_IDS_PATH", tmp_path / "message_ids.json")
    monkeypatch.setattr(scraper, "STATE_PATH", tmp_path / "state.json")
    for env in ("WEBHOOK_URL_GI", "WEBHOOK_URL_UMA", "WEBHOOK_URL_HSR", "WEBHOOK_URL_SUMMARY"):
        monkeypatch.setenv(env, f"https://discord.test/{env}")
    return rec


def gi_cfg(events=ok_source, gacha=None):
    return scraper.GameConfig(
        secret_name="WEBHOOK_URL_GI",
        title="Genshin Impact — Events & Banners",
        role_secret="ROLE_ID_GI",
        events=events,
        gacha=gacha,
    )


def run(cfg, source, ids=None, state=None):
    return scraper.run_flow(
        key="genshin-impact", cfg=cfg, source=source, section_tag="events",
        ids={} if ids is None else ids, state={} if state is None else state, now=NOW,
    )


def test_run_flow_posts_bullets_and_header(calls):
    result = run(gi_cfg(), ok_source)
    assert len(calls["post"]) == 1
    content = calls["post"][0][1]
    assert content.startswith(
        f"**Genshin Impact — Events & Banners — Events**\n<{GI_URL}>\n_Source: **ennead.cc**_"
    )
    assert format_bullet(ITEM) in content
    assert result["status"] == "ok"
    assert result["last_updated"] == "ennead.cc"
    assert result["items"] == 1


def test_run_flow_edits_existing_message(calls):
    ids = {"genshin-impact::events": "123"}
    result = run(gi_cfg(), ok_source, ids=ids)
    assert [c[1] for c in calls["edit"]] == ["123"]
    assert calls["post"] == []
    assert result["action"] == "edited"
    assert ids["genshin-impact::events"] == "123"


def test_run_flow_empty_is_error(calls):
    result = run(gi_cfg(), empty_source)
    assert result["status"] == "error"
    assert result["diag_reason"] == "no_items"
    assert calls["post"] == calls["edit"] == calls["delete"] == []


def test_run_flow_source_exception_is_error(calls):
    ids = {"genshin-impact::events": "123"}
    result = run(gi_cfg(), boom_source, ids=ids)
    assert result["status"] == "error"
    assert "RuntimeError: boom" in result["diag_reason"]
    assert calls["post"] == calls["edit"] == calls["delete"] == []
    assert ids == {"genshin-impact::events": "123"}


def test_run_flow_only_ended_items_posts_placeholder(calls):
    ended = Item("Old", NOW - timedelta(days=9), NOW - timedelta(days=1))
    result = run(gi_cfg(), lambda now: SourceResult([ended], "ennead.cc", GI_URL))
    assert result["status"] == "ok"
    assert "_No current or upcoming items._" in calls["post"][0][1]
    assert result["_normalized_now"] == []


def test_has_changes_ignores_same_source(calls):
    first = run(gi_cfg(), ok_source)
    state = {"genshin-impact::events": {"last_updated": "ennead.cc", "items": first["_normalized_now"]}}
    second = run(gi_cfg(), ok_source, state=state)
    assert second["has_changes"] is False
    assert second["delta_summary"] == "No detected changes"


def test_source_change_is_reported(calls):
    first = run(gi_cfg(), ok_source)
    state = {"genshin-impact::events": {"last_updated": "game8 timestamp", "items": first["_normalized_now"]}}
    second = run(gi_cfg(), ok_source, state=state)
    assert second["has_changes"] is True
    assert "Source changed" in second["delta_summary"]


def test_summary_embed_error_field():
    results = [{
        "key": "genshin-impact::events", "title": "GI — Events", "url": GI_URL,
        "status": "error", "action": "none", "items": 0, "diag_reason": "x" * 300,
    }]
    embed = scraper.make_summary_embed(results)
    value = embed["fields"][0]["value"]
    assert value.startswith("❌ Error: `" + "x" * 200 + "`")
    assert f"[Source]({GI_URL})" in value
    assert embed["color"] == 0xE74C3C


def test_main_one_source_error_others_post(calls, monkeypatch):
    games = {
        "genshin-impact": gi_cfg(events=boom_source),
        "honkai-star-rail": scraper.GameConfig(
            secret_name="WEBHOOK_URL_HSR", title="Honkai: Star Rail — Events & Banners",
            role_secret="ROLE_ID_HSR", events=ok_source, gacha=None,
        ),
    }
    monkeypatch.setattr(scraper, "GAMES", games)
    with pytest.raises(SystemExit) as exc:
        scraper.main()
    assert exc.value.code == 1

    assert [c[0] for c in calls["post"]] == ["https://discord.test/WEBHOOK_URL_HSR"]
    state = json.loads(scraper.STATE_PATH.read_text())
    assert "honkai-star-rail::events" in state
    assert state["honkai-star-rail::events"]["last_updated"] == "ennead.cc"
    assert "genshin-impact::events" not in state
    ids = json.loads(scraper.MESSAGE_IDS_PATH.read_text())
    assert "honkai-star-rail::events" in ids and "genshin-impact::events" not in ids

    assert len(calls["embed"]) == 1
    fields = calls["embed"][0][1]["fields"]
    assert any(f["value"].startswith("❌") for f in fields)


def _uma_games():
    return {
        "genshin-impact": gi_cfg(),
        "umamusume": scraper.GameConfig(
            secret_name="WEBHOOK_URL_UMA", title="Umamusume: Pretty Derby — Events & Banners",
            role_secret="ROLE_ID_UMA", events=ok_source, gacha=ok_source,
        ),
    }


def test_only_key_filters(calls, monkeypatch):
    monkeypatch.setattr(scraper, "GAMES", _uma_games())
    monkeypatch.setattr(scraper, "ONLY_KEY", "umamusume")
    scraper.main()
    urls = {c[0] for c in calls["post"]}
    assert urls == {"https://discord.test/WEBHOOK_URL_UMA"}
    assert len(calls["post"]) == 2
    state = json.loads(scraper.STATE_PATH.read_text())
    assert set(state) == {"umamusume::events", "umamusume::gacha"}


def test_only_key_unknown_does_nothing(calls, monkeypatch):
    monkeypatch.setattr(scraper, "GAMES", _uma_games())
    monkeypatch.setattr(scraper, "ONLY_KEY", "nope")
    scraper.main()
    assert calls["post"] == [] and calls["embed"] == []


def test_disable_uma_events(calls, monkeypatch):
    monkeypatch.setattr(scraper, "GAMES", _uma_games())
    monkeypatch.setattr(scraper, "DISABLE_UMA_EVENTS", True)
    scraper.main()
    state = json.loads(scraper.STATE_PATH.read_text())
    assert set(state) == {"genshin-impact::events", "umamusume::gacha"}


def test_disable_uma_events_with_only_key_keeps_gacha(calls, monkeypatch):
    monkeypatch.setattr(scraper, "GAMES", _uma_games())
    monkeypatch.setattr(scraper, "DISABLE_UMA_EVENTS", True)
    monkeypatch.setattr(scraper, "ONLY_KEY", "umamusume")
    scraper.main()
    state = json.loads(scraper.STATE_PATH.read_text())
    assert set(state) == {"umamusume::gacha"}


def test_games_config_keys_and_titles():
    assert list(scraper.GAMES) == [
        "wuthering-waves", "honkai-star-rail", "umamusume", "genshin-impact",
        "arknights-endfield", "nte", "aniimo",
    ]
    assert scraper.GAMES["nte"].title == "Neverness to Everness — Events & Banners"
    assert scraper.GAMES["nte"].secret_name == "WEBHOOK_URL_NTE"
    assert scraper.GAMES["nte"].role_secret == "ROLE_ID_NTE"
    assert scraper.GAMES["aniimo"].title == "Aniimo — Events"
    assert scraper.GAMES["aniimo"].secret_name == "WEBHOOK_URL_ANIIMO"
    assert scraper.GAMES["aniimo"].role_secret == "ROLE_ID_ANIIMO"
    assert scraper.GAMES["aniimo"].gacha is None
    assert sum(1 for c in scraper.GAMES.values() if c.events) == 7
    assert sum(1 for c in scraper.GAMES.values() if c.gacha) == 6
