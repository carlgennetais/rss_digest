"""One-off model comparison on real data. Read-only towards FreshRSS, Wallabag and the vault.

uv run python -m rss_digest.bench --config c.toml rank --models a,b --days 21
uv run python -m rss_digest.bench --config c.toml write --models a,b,c --topic "Kubernetes=k8s" --out bench-out
"""
import argparse
import random
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .cli import _config_text
from .config import load_config
from .freshrss import FreshRSS
from .interests import parse_interests, rank
from .llm import LLM, LLMError
from .state import State
from .textutil import safe_filename
from .vault import _write_page
from .wallabag import Wallabag
from .webdav import WebDAV

TOP_K = 15


def recall_at_k(items, starred: set[str], ranker, k: int = TOP_K) -> tuple[int, int]:
    """Ranks each day's items like the digest does; returns (starred items in a daily top k, starred items)."""
    days = defaultdict(list)
    for it in items:
        days[it.published.date()].append(it)
    hits = total = 0
    for day_items in days.values():
        stars = {i.id for i in day_items} & starred
        if not stars:  # nothing to measure, so don't pay for ranking it
            continue
        top = {i.id for i in ranker(day_items)[:k]}
        hits, total = hits + len(stars & top), total + len(stars)
    return hits, total


def write_blind(out: Path, title: str, pages: dict[str, str]) -> dict[str, str]:
    """Writes each model's page as A.md, B.md... in random order; returns {letter: model}."""
    models = list(pages)
    random.shuffle(models)
    d = out / safe_filename(title)
    d.mkdir(parents=True, exist_ok=True)
    key = {}
    for letter, model in zip("ABCDEFGH", models):
        (d / f"{letter}.md").write_text(pages[model], encoding="utf-8")
        key[letter] = model
    return key


class _MemDav:
    def __init__(self):
        self.files = {}

    def read(self, path):
        return self.files.get(path)

    def write(self, path, text):
        self.files[path] = text


def _llm(conf, max_tokens):
    llm = LLM(conf.llm)
    llm.limit = max_tokens
    return llm


def bench_rank(conf, models, days, max_tokens):
    fresh = FreshRSS(conf.freshrss)
    items = fresh.reading_list(since=datetime.now(UTC) - timedelta(days=days))
    starred = {i.id for i in fresh.starred()}
    interests = parse_interests(_config_text(WebDAV(conf.nextcloud), "interests.md"))
    for model in models:
        llm = _llm(conf, max_tokens)
        hits, total = recall_at_k(items, starred, lambda its: [i for i, _ in rank(its, interests, "", llm, model)])
        print(f"{model}: {hits}/{total} starred articles in the daily top {TOP_K} "
              f"({hits / total:.0%}), {llm.tokens_used} tokens" if total else f"{model}: no starred articles found")


def bench_write(conf, models, topics, out, max_tokens):
    entries = Wallabag(conf.wallabag).entries(None)
    page_prompt = _config_text(WebDAV(conf.nextcloud), "page-prompt.md")
    with open(out / "key.txt", "a", encoding="utf-8") as keyfile:
        for spec in topics:
            title, _, tag = spec.partition("=")
            items = sorted((e for e in entries if tag in e.tags), key=lambda e: e.published)[-8:]
            if not items:
                print(f"{title}: no Wallabag entries tagged {tag!r}")
                continue
            pages = {}
            for model in models:
                llm, dav = _llm(conf, max_tokens), _MemDav()
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        _write_page(title, None, items, dav, State(Path(tmp) / "s.db"), llm, model, page_prompt)
                except LLMError as e:
                    print(f"{title} / {model}: failed ({e})")
                    continue
                pages[model] = next(iter(dav.files.values()))
                print(f"{title} / {model}: {llm.tokens_used} tokens")
            key = write_blind(out, title, pages)
            keyfile.write(f"{title}: " + ", ".join(f"{k}={m}" for k, m in key.items()) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rss_digest.bench")
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--max-tokens", type=int, default=1_000_000, help="cap per model")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rank")
    r.add_argument("--models", required=True)
    r.add_argument("--days", type=int, default=21)
    w = sub.add_parser("write")
    w.add_argument("--models", required=True)
    w.add_argument("--topic", action="append", required=True, help='"Title=wallabag-tag", repeatable')
    w.add_argument("--out", type=Path, default=Path("bench-out"))
    args = ap.parse_args(argv)
    conf = load_config(args.config)
    models = args.models.split(",")
    if args.cmd == "rank":
        bench_rank(conf, models, args.days, args.max_tokens)
    else:
        args.out.mkdir(parents=True, exist_ok=True)
        bench_write(conf, models, args.topic, args.out, args.max_tokens)


if __name__ == "__main__":
    main()
