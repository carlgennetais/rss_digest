import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

import yaml

MARKER = "## My notes"
_MARKER_LINE = re.compile(r"^## My notes[ \t\r]*$", re.MULTILINE)
_AI_HEADINGS = ("## Summary", "## Recent changes", "## Sources")


class PageParseError(ValueError):
    pass


@dataclass
class Page:
    front: dict
    summary: str = ""
    recent: str = ""
    my_notes_tail: str = f"{MARKER}\n"


def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _split_front(text: str) -> tuple[dict, str]:
    m = re.match(r"---[ \t]*\r?\n(.*?\r?\n)?---[ \t]*(?:\r?\n|\Z)", text, re.DOTALL)
    if not m:
        raise PageParseError("missing or unterminated frontmatter")
    try:
        front = yaml.safe_load(m.group(1) or "")
    except yaml.YAMLError as e:
        raise PageParseError(f"bad frontmatter YAML: {e}") from e
    if not isinstance(front, dict):
        raise PageParseError("frontmatter is not a mapping")
    if front.get("type") != "topic":
        raise PageParseError("not a topic page")
    return front, text[m.end():]


def is_topic_page(text: str) -> bool:
    try:
        _split_front(text)
    except PageParseError:
        return False
    return True


def parse_page(text: str) -> Page:
    front, body = _split_front(text)
    marks = list(_MARKER_LINE.finditer(body))
    if len(marks) != 1:
        raise PageParseError(f"expected exactly one '{MARKER}' line, found {len(marks)}")
    tail = body[marks[0].start():]
    ai = body[:marks[0].start()]
    positions = []
    for h in _AI_HEADINGS:
        found = list(re.finditer(rf"^{re.escape(h)}[ \t\r]*$", ai, re.MULTILINE))
        if len(found) != 1:
            raise PageParseError(f"missing or repeated heading {h}")
        positions.append(found[0])
    if [p.start() for p in positions] != sorted(p.start() for p in positions):
        raise PageParseError("AI headings out of order")
    s, r, so = positions
    front["updated"] = _iso(front.get("updated"))
    srcs = front.get("sources") or []
    front["sources"] = [{**x, "date": _iso(x.get("date"))} if "date" in x else x for x in srcs]
    return Page(front, ai[s.end():r.start()].strip(), ai[r.end():so.start()].strip(), tail)


def _ai_part(page: Page) -> str:
    f = yaml.safe_dump(page.front, sort_keys=False, allow_unicode=True)
    src = "\n".join(f"- [{s['title']}]({s['url']}) — {s['date']}" for s in page.front.get("sources", []))
    return (f"---\n{f}---\n## Summary\n{page.summary.strip()}\n\n"
            f"## Recent changes\n{page.recent.strip()}\n\n## Sources\n{src}\n\n")


def render_page(page: Page) -> str:
    return _ai_part(page) + page.my_notes_tail


def new_page(title: str) -> Page:
    front = {"type": "topic", "title": title, "description": "",
             "updated": date.today().isoformat(), "sources": []}
    return Page(front)


def ai_hash(text: str) -> str:
    m = _MARKER_LINE.search(text)
    return hashlib.sha256((text[:m.start()] if m else text).encode()).hexdigest()
