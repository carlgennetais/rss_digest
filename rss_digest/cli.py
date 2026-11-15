import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

from .config import load_config
from .digest import build_digest
from . import backlog
from .feed import update_feed
from .fetch import enrich
from .freshrss import FreshRSS
from .interests import filter_items, parse_interests
from .llm import LLM, BudgetExceeded
from .state import State
from .vault import list_topics, profile_text, select_new_kept, update_vault
from .wallabag import Wallabag
from .webdav import WebDAV

log = logging.getLogger(__name__)
DIGEST_RESERVE = 30_000  # tokens the vault step leaves for the digest

CONFIG_FILES = ("interests.md", "digest-prompt.md", "page-prompt.md")


def _default(name: str) -> str:
    return resources.files("rss_digest").joinpath("defaults", name).read_text(encoding="utf-8")


def seed_config(dav) -> None:
    for name in CONFIG_FILES:
        if dav.read(f"_config/{name}") is None:
            dav.write(f"_config/{name}", _default(name))


def _config_text(dav, name: str) -> str:
    text = dav.read(f"_config/{name}")
    return _default(name) if text is None else text


def run_daily(conf, fresh, wb, dav, llm, state, now: datetime) -> None:
    seed_config(dav)
    interests = parse_interests(_config_text(dav, "interests.md"))
    digest_prompt = _config_text(dav, "digest-prompt.md")
    page_prompt = _config_text(dav, "page-prompt.md")
    default_since = now - timedelta(hours=24)

    def since(key):
        v = state.get(key)
        return datetime.fromisoformat(v) if v else default_since

    flagged: list[str] = []
    notice, vault_error = None, None
    kept = []
    # the vault step keeps at least half the budget, even when it is below the reserve
    limit, llm.limit = llm.limit, llm.limit - min(DIGEST_RESERVE, llm.limit // 2)
    try:
        kept = enrich(select_new_kept(fresh.starred() + wb.entries(since("last_kept_run")), state))
        if kept:
            flagged = update_vault(kept, dav, state, llm, conf.llm.cheap_model, conf.llm.writer_model, page_prompt)
    except BudgetExceeded:
        notice, vault_error = "Vault update paused: token budget reached.", True
    except Exception as e:
        log.exception("vault update failed")
        notice, vault_error = "Vault update failed (see logs); will retry next run.", e
    finally:
        llm.limit = limit
    # Wallabag is fetched since last_kept_run, so only advance it once every Wallabag item is on its pages.
    if vault_error is None and all(state.is_processed(i.id) for i in kept if i.source == "wallabag"):
        state.set("last_kept_run", now.isoformat())

    items = fresh.reading_list(since=since("last_digest_run"))
    kept_items, muted = filter_items(items, interests, state)
    md = build_digest(now.date(), kept_items, interests, profile_text(list_topics(dav)), llm,
                      conf.llm.cheap_model, conf.llm.writer_model, digest_prompt,
                      conf.digest.top_n, conf.digest.also_notable, flagged, muted=muted)
    if notice:
        md += f"\n{notice}\n"
    dav.write(f"Digest/{now.date().isoformat()}.md", md)
    state.set("last_digest_run", now.isoformat())
    try:  # the feed is a convenience copy: a failure here must not fail the run
        dav.write("Feed/digest.xml", update_feed(dav.read("Feed/digest.xml"), now.date(), md, now))
    except Exception:
        log.exception("feed update failed")
    if isinstance(vault_error, Exception):
        raise vault_error


def run_backlog(conf, fresh, wb, dav, llm, state) -> None:
    seed_config(dav)
    page_prompt = _config_text(dav, "page-prompt.md")
    items = enrich(select_new_kept(fresh.starred() + wb.entries(None), state))
    log.info("Backlog: %d new items to process", len(items))
    try:
        update_vault(items, dav, state, llm, conf.llm.cheap_model, conf.llm.writer_model, page_prompt)
    except BudgetExceeded:
        log.info("Token budget reached; re-run to continue.")
        return
    log.info("Backlog done.")


def run_export(fresh, wb, state, out: Path) -> int:
    items = enrich(select_new_kept(fresh.starred() + wb.entries(None), state))
    n = backlog.export(items, out)
    log.info("Exported %d items to %s", n, out)
    return n

def run_import(dav, state, folder: Path, state_path: Path) -> dict[str, int]:
    counts = backlog.import_pages(folder, dav, state, _config_text(dav, "page-prompt.md"))
    print(", ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"Still to write: {folder / 'todo.txt'}")
    if counts["written"]:
        print(f"Copy {state_path} to the server before enabling the daily timer.")
    return counts

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rss-digest")
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("command", choices=["run", "backlog", "backlog-export", "backlog-import"])
    ap.add_argument("--dir", type=Path, default=Path("backlog"), help="backlog-export/-import folder")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods the journal
    try:
        conf = load_config(args.config)
        clients = (FreshRSS(conf.freshrss), Wallabag(conf.wallabag), WebDAV(conf.nextcloud),
                   LLM(conf.llm), State(conf.state_path))
        fresh, wb, dav, llm, state = clients
        if args.command == "run":
            run_daily(conf, *clients, now=datetime.now(UTC))
        elif args.command == "backlog-export":
            run_export(fresh, wb, state, args.dir)
        elif args.command == "backlog":
            run_backlog(conf, *clients)
        else:
            run_import(dav, state, args.dir, conf.state_path)
    except Exception:
        log.exception("rss-digest failed")
        return 1
    return 0
