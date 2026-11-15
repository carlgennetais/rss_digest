"""One-off model comparison on real data. Read-only towards FreshRSS, Wallabag and the vault.

uv run python -m rss_digest.bench --config c.toml rank --models a,b --days 21
uv run python -m rss_digest.bench --config c.toml write --models a,b,c --topic "Kubernetes=k8s" --out bench-out
"""
import argparse
import logging
import random
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .cli import _config_text
from .config import load_config
from .freshrss import FreshRSS
import httpx

from .interests import BATCH, parse_interests, rank
from .llm import LLM, LLMError
from .state import State
from .textutil import safe_filename
from .vault import _write_page
from .wallabag import Wallabag
from .webdav import WebDAV

TOP_K = 15
DAILY_ARTICLES = 45  # measured on full FreshRSS days, 2026-11-15
log = logging.getLogger(__name__)


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


def jev_rank(items, interests, base_url: str, api_key: str, model: str, client: httpx.Client | None = None):
    """Ranks with a JEV decision model (OpenRouter's alpha decisions endpoint): one yes/no question per article,
    scored 0-10 from P(yes). Returns (ranked [(item, score)], cost in USD)."""
    client = client or httpx.Client(timeout=120)
    url = base_url.split("/api/")[0] + "/api/alpha/decisions"
    reader = {"follow": interests.follow, "mute": interests.mute}
    scored, cost = [], 0.0
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        ids = {f"a{n}": it for n, it in enumerate(batch, 1)}
        body = {
            "model": model,
            "state": {"reader": reader, "articles": {k: {"feed": it.feed, "title": it.title, "excerpt": it.text[:300]}
                                                     for k, it in ids.items()}},
            "questions": {k: {"type": "noul", "instructions": f"Would this reader want to read article {k}?",
                              "criteria": {"true": "It matches what the reader follows and nothing they mute",
                                           "false": "It does not match, or it matches something they mute"}}
                          for k in ids},
        }
        try:
            r = client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=body)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:  # same as rank(): a failed batch scores 0
            log.warning("jev batch failed, scoring 0: %s", e)
            data = {}
        cost += (data.get("usage") or {}).get("cost") or 0
        answers = data.get("answers") or {}
        for k, it in ids.items():
            p = (answers.get(k) or {}).get("noul")
            scored.append((it, round(10 * p, 2) if isinstance(p, (int, float)) else 0.0))
    return sorted(scored, key=lambda x: -x[1]), cost


def auc(pos: list[float], neg: list[float]) -> float:
    """Probability that a random positive scores above a random negative; ties count half."""
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def mixed_days(pos, neg, size: int = DAILY_ARTICLES, seed: int = 0) -> list[list]:
    """Shuffles positives and negatives together into simulated days of `size` articles."""
    pool = list(pos) + list(neg)
    random.Random(seed).shuffle(pool)
    return [pool[i:i + size] for i in range(0, len(pool), size)]


def _rank_with(model, items, interests, conf, llm) -> tuple[list, float]:
    """Returns ([(item, score)] best first, extra cost not already counted in llm.cost)."""
    if "typesafe/jev" in model:
        return jev_rank(items, interests, conf.llm.base_url, conf.llm.api_key, model)
    return rank(items, interests, "", llm, model), 0.0


def bench_rank(conf, models, days, max_tokens, out: Path):
    fresh = FreshRSS(conf.freshrss)
    items = fresh.reading_list(since=datetime.now(UTC) - timedelta(days=days))
    starred = {i.id for i in fresh.starred()}
    interests = parse_interests(_config_text(WebDAV(conf.nextcloud), "interests.md"))
    with open(out / "ranking.tsv", "w", encoding="utf-8") as tsv:
        tsv.write("model\tday\trank\tscore\tstarred\tin_top\tfeed\ttitle\turl\n")
        for model in models:
            llm = _llm(conf, max_tokens)
            jev_cost, n_ranked = [0.0], [0]

            def ranker(its, llm=llm, model=model, jev_cost=jev_cost, n_ranked=n_ranked):
                ranked, c = _rank_with(model, its, interests, conf, llm)
                jev_cost[0] += c
                n_ranked[0] += len(ranked)
                for n, (i, score) in enumerate(ranked, 1):
                    title = " ".join(i.title.split())
                    tsv.write(f"{model}\t{i.published.date()}\t{n}\t{score:g}\t{'*' if i.id in starred else ''}\t"
                              f"{'yes' if n <= TOP_K else ''}\t{i.feed}\t{title}\t{i.url}\n")
                return [i for i, _ in ranked]

            hits, total = recall_at_k(items, starred, ranker)
            cost = llm.cost + jev_cost[0]
            month = cost / n_ranked[0] * DAILY_ARTICLES * 30 if n_ranked[0] else 0
            print(f"{model}: {hits}/{total} starred in daily top {TOP_K}, {n_ranked[0]} articles ranked, "
                  f"${cost:.4f} (≈ ${month:.2f}/month at {DAILY_ARTICLES} articles/day)")


def bench_mix(conf, models, n_stars, from_weeks, to_weeks, max_tokens, out: Path, seed: int = 0):
    """Stars (positives) and read, unstarred articles (negatives) published in the same window,
    from `from_weeks` to `to_weeks` weeks ago, so both sides cover the same topics and reading habits."""
    fresh = FreshRSS(conf.freshrss)
    now = datetime.now(UTC)
    stars = fresh.starred()
    star_ids = {s.id for s in stars}
    start, end = now - timedelta(weeks=to_weeks), now - timedelta(weeks=from_weeks)
    in_window = lambda i: start <= i.published < end  # noqa: E731
    # filtering on publication date also drops archives that were imported and bulk-marked read
    recent = [i for i in fresh.reading_list(since=start) if i.id not in star_ids and in_window(i)]
    neg = [i for i in recent if i.read]  # unread means not seen yet, so not a judgement either way
    window_stars = [s for s in stars if in_window(s)]
    pos = random.Random(seed).sample(window_stars, min(n_stars, len(window_stars)))
    interests = parse_interests(_config_text(WebDAV(conf.nextcloud), "interests.md"))
    sim = mixed_days(pos, neg, seed=seed)
    lines = [f"# Ranking on a mixed sample ({now.date()})", "",
             f"{len(pos)} stars + {len(neg)} read, unstarred articles, all published {to_weeks} to {from_weeks} weeks ago "
             f"({len(recent) - len(neg)} unread ones left out), "
             f"shuffled into {len(sim)} simulated days of up to {DAILY_ARTICLES}. Seed {seed}.", "",
             "| model | AUC | stars in daily top 15 | median star rank | cost | ≈ per month |", "|---|---:|---:|---:|---:|---:|"]
    with open(out / "ranking-mix.tsv", "w", encoding="utf-8") as tsv:
        tsv.write("model\tsim_day\trank\tscore\tstarred\tfeed\ttitle\turl\n")
        for model in models:
            llm, extra, scores, ranks = _llm(conf, max_tokens), 0.0, {}, []
            for d, day in enumerate(sim, 1):
                ranked, c = _rank_with(model, day, interests, conf, llm)
                extra += c
                for n, (i, score) in enumerate(ranked, 1):
                    scores[i.id] = score
                    if i.id in star_ids:
                        ranks.append(n)
                    title = " ".join(i.title.split())
                    tsv.write(f"{model}\t{d}\t{n}\t{score:g}\t{'*' if i.id in star_ids else ''}\t{i.feed}\t{title}\t{i.url}\n")
            a = auc([scores[i.id] for i in pos], [scores[i.id] for i in neg])
            cost = llm.cost + extra
            month = cost / len(scores) * DAILY_ARTICLES * 30
            top = sum(r <= TOP_K for r in ranks)
            row = (f"| {model} | {a:.2f} | {top}/{len(ranks)} | {sorted(ranks)[len(ranks) // 2]} | "
                   f"${cost:.4f} | ${month:.2f} |")
            print(row)
            lines.append(row)
    (out / "ranking-mix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
                print(f"{title} / {model}: {llm.tokens_used} tokens, ${llm.cost:.4f}")
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
    r.add_argument("--out", type=Path, default=Path("bench-out"))
    x = sub.add_parser("rank-mix", help="stars vs read, unstarred articles published in the same window")
    x.add_argument("--models", required=True)
    x.add_argument("--stars", type=int, default=150)
    x.add_argument("--from-weeks", type=int, default=4, help="skip the most recent weeks")
    x.add_argument("--to-weeks", type=int, default=26)
    x.add_argument("--seed", type=int, default=0)
    x.add_argument("--out", type=Path, default=Path("bench-out"))
    w = sub.add_parser("write")
    w.add_argument("--models", required=True)
    w.add_argument("--topic", action="append", required=True, help='"Title=wallabag-tag", repeatable')
    w.add_argument("--out", type=Path, default=Path("bench-out"))
    args = ap.parse_args(argv)
    conf = load_config(args.config)
    models = args.models.split(",")
    args.out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "rank-mix":
        bench_mix(conf, models, args.stars, args.from_weeks, args.to_weeks, args.max_tokens, args.out, args.seed)
    elif args.cmd == "rank":
        bench_rank(conf, models, args.days, args.max_tokens, args.out)
    else:
        bench_write(conf, models, args.topic, args.out, args.max_tokens)


if __name__ == "__main__":
    main()
