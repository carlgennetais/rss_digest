import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
