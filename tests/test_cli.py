import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from conftest import FakeDav, FakeLLM, item

from rss_digest import cli
from rss_digest.cli import main, run_backlog, run_daily, seed_config
from rss_digest.config import Config, DigestConf, FreshRSSConf, LLMConf, NextcloudConf, WallabagConf
from rss_digest.llm import BudgetExceeded

NOW = datetime(2026, 11, 14, 6, 0, tzinfo=UTC)
CONF = Config(
    freshrss=FreshRSSConf("u", "u", "p"), wallabag=WallabagConf("u", "i", "s", "u", "p"),
    nextcloud=NextcloudConf("u", "u", "p", "v"), llm=LLMConf("u", "k", "cheap", "writer"),
    state_path=Path("unused"), digest=DigestConf(),
)


class FakeFresh:
    def __init__(self, reading=(), starred=()):
        self.reading, self._starred = list(reading), list(starred)
        self.reading_list_since = None
        self.reading_list_raises = None

    def reading_list(self, since):
        self.reading_list_since = since
        if self.reading_list_raises:
            raise self.reading_list_raises
        return self.reading

    def starred(self):
        return self._starred


class FakeWallabag:
    def __init__(self, entries=()):
        self._entries = list(entries)
        self.since = "unset"

    def entries(self, since):
        self.since = since
        return self._entries


@pytest.fixture
def env(state):
    return SimpleNamespace(fresh=FakeFresh(), wb=FakeWallabag(), dav=FakeDav(), llm=FakeLLM(), state=state)


def daily(env):
    run_daily(CONF, env.fresh, env.wb, env.dav, env.llm, env.state, now=NOW)


def test_first_run_digests_only_last_24h(env):
    daily(env)
    assert env.fresh.reading_list_since == NOW - timedelta(hours=24)
    assert env.wb.since == NOW - timedelta(hours=24)
    assert f"Digest/{NOW.date()}.md" in env.dav.files and env.state.get("last_digest_run") == NOW.isoformat()
    assert env.state.get("last_kept_run") == NOW.isoformat()
    assert env.llm.calls == []  # zero-item day: no LLM calls


def test_seed_never_overwrites(env):
    env.dav.files["_config/interests.md"] = "mine"
    seed_config(env.dav)
    assert env.dav.files["_config/interests.md"] == "mine" and "_config/page-prompt.md" in env.dav.files


def test_failure_does_not_advance_timestamps(env):
    env.fresh.reading_list_raises = httpx.ConnectError("down")
    with pytest.raises(httpx.ConnectError):
        daily(env)
    assert env.state.get("last_digest_run") is None


def test_budget_hit_still_writes_digest_with_notice(env, monkeypatch):
    def boom(*a, **k):
        raise BudgetExceeded("x")
    monkeypatch.setattr(cli, "update_vault", boom)
    env.fresh._starred = [item("Starred one")]
    daily(env)
    assert "Vault update paused: token budget reached." in env.dav.files[f"Digest/{NOW.date()}.md"]
    assert env.state.get("last_kept_run") is None and env.state.get("last_digest_run") == NOW.isoformat()


def test_vault_failure_writes_digest_then_reraises(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vault broke")
    monkeypatch.setattr(cli, "update_vault", boom)
    env.fresh._starred = [item("Starred one")]
    with pytest.raises(RuntimeError, match="vault broke"):
        daily(env)
    assert "Vault update failed (see logs); will retry next run." in env.dav.files[f"Digest/{NOW.date()}.md"]
    assert env.state.get("last_kept_run") is None and env.state.get("last_digest_run") == NOW.isoformat()


def test_backlog_budget_returns_normally(env, monkeypatch, caplog):
    def boom(*a, **k):
        raise BudgetExceeded("x")
    monkeypatch.setattr(cli, "update_vault", boom)
    env.fresh._starred = [item("Starred one")]
    with caplog.at_level(logging.INFO):
        run_backlog(CONF, env.fresh, env.wb, env.dav, env.llm, env.state)
    assert "re-run to continue" in caplog.text
    assert env.wb.since is None


def test_main_returns_1_on_bad_config(tmp_path):
    assert main(["--config", str(tmp_path / "missing.toml"), "run"]) == 1


def wb_item(title):
    return replace(item(title, id=f"wallabag:{title}"), source="wallabag")


def test_unwritten_wallabag_item_keeps_since_window(env):
    before = (NOW - timedelta(days=3)).isoformat()
    env.state.set("last_kept_run", before)
    env.wb._entries = [wb_item("w")]
    env.llm = FakeLLM(json_errors=2)
    daily(env)
    assert env.state.get("last_kept_run") == before and not env.state.is_processed("wallabag:w")
    env.llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, {"summary": "s", "recent": "r"}])
    run_daily(CONF, env.fresh, env.wb, env.dav, env.llm, env.state, now=NOW + timedelta(days=1))
    assert env.wb.since == NOW - timedelta(days=3) and env.state.is_processed("wallabag:w")
    assert env.state.get("last_kept_run") == (NOW + timedelta(days=1)).isoformat()


def test_assign_prompt_requires_a_topic_for_every_article():
    from rss_digest.vault import ASSIGN_PROMPT
    assert "Omit" not in ASSIGN_PROMPT and "at least one" in ASSIGN_PROMPT


def test_vault_step_leaves_reserve_for_digest(env):
    class SpyLLM(FakeLLM):
        def _record(self, *a):
            self.limits.append(self.limit)
            super()._record(*a)
    env.fresh._starred = [item("Starred one")]
    env.fresh.reading = [item("news")]
    env.llm = SpyLLM(json_replies=[{"1": ["NEW: T"]}, {"description": "d", "summary": "s", "recent": ""}, {"1": 5}],
                     text_replies=["summary body"])
    env.llm.limits = []
    daily(env)
    assert env.llm.limits == [170_000, 170_000, 200_000, 200_000] and env.llm.limit == 200_000


def test_small_budget_still_gives_vault_a_share(env):
    env.fresh._starred = [item("Starred one")]
    env.fresh.reading = [item("news")]
    env.llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, {"description": "d", "summary": "s", "recent": ""}, {"1": 5}],
                      text_replies=["summary body"], tokens_per_call=5_000)
    env.llm.limit = 20_000
    daily(env)
    assert env.state.is_processed(item("Starred one").id) and "summary body" in env.dav.files[f"Digest/{NOW.date()}.md"]


def test_main_quiets_httpx_request_logs(tmp_path):
    import logging
    main(["--config", str(tmp_path / "missing.toml"), "run"])
    assert logging.getLogger("httpx").level == logging.WARNING


def test_daily_writes_public_feed(env):
    daily(env)
    assert "Digest " + NOW.date().isoformat() in env.dav.files["Feed/digest.xml"]


def test_daily_reads_the_page_of_a_thin_kept_item(env, monkeypatch):
    monkeypatch.setattr("rss_digest.fetch.page_text", lambda url, client=None: "Full page text. " * 100)
    env.wb = FakeWallabag([item("thin")])
    env.llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, {"description": "d", "summary": "s", "recent": ""}])
    daily(env)
    writer_prompt = env.llm.calls[1][3]
    assert "Full page text." in writer_prompt


def test_daily_uses_the_transcript_of_a_saved_video(env, monkeypatch):
    monkeypatch.setattr("rss_digest.fetch.yt_transcript", lambda url: "The talk transcript. " * 50)
    env.wb = FakeWallabag([item("talk", url="https://www.youtube.com/watch?v=x")])
    env.llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, {"description": "d", "summary": "s", "recent": ""}])
    daily(env)
    assert "The talk transcript." in env.llm.calls[1][3]


def test_daily_regenerates_the_index_when_pages_change(env):
    env.wb = FakeWallabag([item("talk", text="t" * 2000)])
    env.llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, {"description": "d", "summary": "s", "recent": ""}])
    daily(env)
    assert "[T](<T.md>)" in env.dav.files["Topics/index.md"]
