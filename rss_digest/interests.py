import logging
import re
from dataclasses import dataclass, field

from rss_digest.llm import LLMError
from rss_digest.models import Item
from rss_digest.textutil import normalize_url, strip_md_images

log = logging.getLogger(__name__)
BATCH = 40
_LINE = re.compile(r"^\s*(follow|mute)\s*:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class Interests:
    follow: list[str] = field(default_factory=list)
    mute: list[str] = field(default_factory=list)
    mute_feeds: list[str] = field(default_factory=list)


def parse_interests(md: str) -> Interests:
    out = Interests()
    for line in md.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kind, value = m.group(1).lower(), m.group(2)
        if kind == "follow":
            out.follow.append(value)
        elif value.lower().startswith("feed:"):
            out.mute_feeds.append(value[5:].strip())
        else:
            out.mute.append(value)
    return out


def _muted(item: Item, interests: Interests) -> bool:
    if item.feed.lower() in (f.lower() for f in interests.mute_feeds):
        return True
    hay = f"{item.title}\n{item.feed}".lower()
    return any(k.lower() in hay for k in interests.mute)


def filter_items(items, interests, state) -> tuple[list[Item], int]:
    kept, muted, seen = [], 0, set()
    for it in items:
        if _muted(it, interests):
            muted += 1
            continue
        u = normalize_url(it.url)
        if u in seen or state.has_key("u:" + u):
            continue
        seen.add(u)
        kept.append(it)
    return kept, muted


def _score(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def rank(items, interests, profile, llm, model) -> list[tuple[Item, float]]:
    system = (
        "Score each article 0-10 for how much this reader would want it. "
        'Reply with JSON only: {"<n>": score}.\n'
        + "".join(f"Follow: {f}\n" for f in interests.follow)
        + "".join(f"Mute: {m}\n" for m in interests.mute)
        + (f"\nReader profile:\n{profile}" if profile else "")
    )
    scored: list[tuple[Item, float]] = []
    for start in range(0, len(items), BATCH):
        batch = items[start : start + BATCH]
        user = "\n".join(
            f"{n}. [{it.feed}] {it.title} — {strip_md_images(it.text)[:300]}" for n, it in enumerate(batch, 1)
        )
        try:
            reply = llm.chat_json(model, system, user)
        except LLMError as e:
            log.warning("ranking batch failed, scoring 0: %s", e)
            reply = {}
        if not isinstance(reply, dict):
            reply = {}
        scored += [(it, _score(reply.get(str(n)))) for n, it in enumerate(batch, 1)]
    return sorted(scored, key=lambda p: -p[1])  # stable: ties keep input order
