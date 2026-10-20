import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import game_codes_scraper as gcs
from game_codes_scraper import GAMES, run

FIXTURES = ROOT / "tests" / "fixtures" / "codes"
CODEX = "https://codex.invalid/hook"
SUMMARY = "https://summary.invalid/hook"

ENDFIELD_STATE = ["ALLFIELD", "ENDFIELD4PC", "ENDFIELDGIFT", "ENDFIELDRENEW", "RETURNOFALL", "ZAU2SYXHWX5L4ZH"]


def _fx(name): return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


class Env:
    def __init__(self, monkeypatch, tmp_path, game):
        self.game = game
        self.pages = {}
        self.fetched = []
        self.posts = []
        self.post_result = "id"
        self.state_path = tmp_path / "state.json"
        monkeypatch.setitem(GAMES[game], "state_path", self.state_path)
        monkeypatch.setenv("WEBHOOK_URL_CODEX", CODEX)
        monkeypatch.setenv("WEBHOOK_URL_SUMMARY", SUMMARY)
        monkeypatch.setenv(GAMES[game]["role_env"], "123")
        monkeypatch.delenv("DRY_RUN", raising=False)
        monkeypatch.setattr(gcs, "fetch_html", self._fetch)
        monkeypatch.setattr(gcs, "post_webhook", self._post)

    def _fetch(self, url):
        self.fetched.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    def _post(self, webhook, content):
        self.posts.append((webhook, content))
        return self.post_result

    def set_pages(self, primary, fallback=None):
        g = GAMES[self.game]
        self.pages[g["primary"]] = primary
        if fallback is not None:
            self.pages[g["fallback"]] = fallback

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def codex(self):
        return [c for w, c in self.posts if w == CODEX]

    def summary(self):
        return [c for w, c in self.posts if w == SUMMARY]


@pytest.fixture
def aniimo(monkeypatch, tmp_path):
    env = Env(monkeypatch, tmp_path, "aniimo")
    env.set_pages(_fx("pockettactics_aniimo"), _fx("pocketgamer_aniimo"))
    return env


def test_first_run_posts_all_active_with_single_ping(aniimo):
    assert run("aniimo") == 0
    posts = aniimo.codex()
    assert len(posts) == 16
    assert sum("<@&123>" in p for p in posts) == 1 and "<@&123>" in posts[0]
    assert posts[0].count("New Code Found!") == 1
    assert "**Aniimo — New Code Found!**" in posts[0]
    assert GAMES["aniimo"]["primary"] in posts[0]
    assert aniimo.state()["last_source"] == "primary"
    assert len(aniimo.state()["seen_codes"]) == 16


def test_run_skips_seen_code_case_insensitive(aniimo):
    aniimo.write_state({"seen_codes": ["ANIIMOIDYLL"], "last_source": "primary"})
    assert run("aniimo") == 0
    assert not any("`aniimoidyll`" in p.lower() for p in aniimo.codex())
    assert len(aniimo.codex()) == 15


def test_endfield_existing_state_only_new_code_posted(monkeypatch, tmp_path):
    env = Env(monkeypatch, tmp_path, "endfield")
    env.set_pages(_fx("pockettactics_endfield"), _fx("pocketgamer_endfield"))
    env.write_state({"seen_codes": ENDFIELD_STATE, "last_health_ping_iso": "2026-10-12T17:21:04.997054-03:00"})
    assert run("endfield") == 0
    posts = env.codex()
    assert len(posts) == 1 and "`AKENDFIELDSUB`" in posts[0]
    state = env.state()
    assert "AKENDFIELDSUB" in state["seen_codes"] and "ALLFIELD" in state["seen_codes"]
    assert state["last_health_ping_iso"] == "2026-10-12T17:21:04.997054-03:00"


def test_primary_fail_uses_fallback_and_warns_once(aniimo):
    aniimo.pages[GAMES["aniimo"]["primary"]] = RuntimeError("boom")
    aniimo.write_state({"seen_codes": ["X"], "last_source": "primary"})
    assert run("aniimo") == 0
    assert len(aniimo.summary()) == 1 and "using fallback" in aniimo.summary()[0]
    assert aniimo.state()["last_source"] == "fallback"
    assert GAMES["aniimo"]["fallback"] in aniimo.codex()[0]
    assert run("aniimo") == 0
    # the second run may send a health ping, but no new fallback warning
    assert len([s for s in aniimo.summary() if "using fallback" in s]) == 1


def test_primary_returns_zero_codes_counts_as_failure(aniimo):
    aniimo.pages[GAMES["aniimo"]["primary"]] = "<html><body>Just a moment...</body></html>"
    aniimo.write_state({"seen_codes": ["X"], "last_source": "primary"})
    assert run("aniimo") == 0
    assert aniimo.state()["last_source"] == "fallback"
    assert GAMES["aniimo"]["fallback"] in aniimo.fetched


def test_first_run_primary_fail_posts_nothing(aniimo):
    aniimo.pages[GAMES["aniimo"]["primary"]] = RuntimeError("boom")
    assert run("aniimo") == 1
    assert aniimo.codex() == []
    assert GAMES["aniimo"]["fallback"] not in aniimo.fetched


def test_run_both_sources_fail_exits_1(aniimo):
    aniimo.pages[GAMES["aniimo"]["primary"]] = RuntimeError("boom")
    aniimo.pages[GAMES["aniimo"]["fallback"]] = RuntimeError("bang")
    aniimo.write_state({"seen_codes": ["X"], "last_source": "primary"})
    assert run("aniimo") == 1
    assert len(aniimo.summary()) == 1 and "no source available" in aniimo.summary()[0]
    assert aniimo.state()["last_source"] == "none"
    assert aniimo.state()["seen_codes"] == ["X"]


def test_recovery_warns_primary_back(aniimo):
    aniimo.write_state({"seen_codes": ["X"], "last_source": "fallback"})
    assert run("aniimo") == 0
    assert any("primary source is back" in s for s in aniimo.summary())
    assert aniimo.state()["last_source"] == "primary"


def test_failed_post_not_marked_seen(aniimo):
    aniimo.write_state({"seen_codes": ["X"], "last_source": "primary"})
    aniimo.post_result = None
    run("aniimo")
    assert aniimo.state()["seen_codes"] == ["X"]


def test_dry_run_does_not_write_state(aniimo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    assert run("aniimo") == 0
    assert not aniimo.state_path.exists()


def test_nte_fallback_does_not_post_codes_missing_from_primary(monkeypatch, tmp_path):
    env = Env(monkeypatch, tmp_path, "nte")
    env.set_pages(_fx("pockettactics_nte"), _fx("pocketgamer_nte"))
    primary_codes = [c["code"] for c in gcs.parse_codes(_fx("pockettactics_nte"))]
    env.write_state({"seen_codes": sorted(c.upper() for c in primary_codes), "last_source": "primary"})
    assert run("nte") == 0 and env.codex() == []
    # a later day: primary is down, fallback lists many codes the primary treats as expired
    env.pages[GAMES["nte"]["primary"]] = RuntimeError("blocked")
    assert run("nte") == 0
    assert env.codex() == []
