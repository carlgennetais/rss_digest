"""Backlog import with pages written by Claude Code agents instead of the LLM API.

backlog-export writes the kept items to a local folder, agents write one JSON per topic page there,
and backlog-import writes those pages to the vault through the same code as the daily run.
"""
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .fetch import THIN, is_video
from .models import Item
from .textutil import safe_filename
from .vault import mark_kept, write_topic_page

log = logging.getLogger(__name__)
TEXT_CAP = 3000  # the same cap the API page writer uses


def vtt_to_text(vtt: str) -> str:
    """Subtitle text without the header, cue numbers, timings and tags; auto-captions repeat lines."""
    out: list[str] = []
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line == "WEBVTT" or line.isdigit() or "-->" in line or line.startswith(("Kind:", "Language:")):
            continue
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


def yt_transcript(url: str) -> str | None:
    """English or French subtitles of a video via yt-dlp, or None if unavailable."""
    if shutil.which("yt-dlp") is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            # no check=True: yt-dlp exits non-zero when one of several matching tracks fails (often HTTP 429),
            # even though it already saved a usable one
            subprocess.run(["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs", "--sleep-subtitles", "1",
                            "--sub-langs", "en.*,fr.*", "--sub-format", "vtt", "-o", f"{tmp}/t", url],
                           capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return None
        vtts = sorted(Path(tmp).glob("*.vtt"))
        return vtt_to_text(vtts[0].read_text(encoding="utf-8", errors="replace")) if vtts else None


def export(items, out: Path, transcript=None) -> int:
    """Writes out/items/<n>.json and out/titles.tsv; returns the number of items."""
    if (out / "assignment.json").exists():
        # item numbers would change and assignment.json would point at the wrong articles
        raise FileExistsError(f"{out / 'assignment.json'} exists: topics were already assigned to this export")
    (out / "items").mkdir(parents=True, exist_ok=True)
    rows = ["n\tsource\tdate\tfeed\ttags\tthin\ttitle"]
    found = 0
    for n, it in enumerate(items, 1):
        video, text = is_video(it.url), it.text
        if video and transcript and len(text) < THIN and (t := transcript(it.url)):
            text, found = t, found + 1
        text = text[:TEXT_CAP]
        thin = len(text) < THIN
        (out / "items" / f"{n}.json").write_text(json.dumps({
            "id": it.id, "source": it.source, "url": it.url, "title": it.title, "feed": it.feed,
            "tags": list(it.tags), "published": it.published.isoformat(), "text": text,
            "thin": thin, "video": video,
        }, ensure_ascii=False), encoding="utf-8")
        clean = lambda s: " ".join(str(s).split())  # noqa: E731 - no tabs or newlines inside a TSV cell
        rows.append(f"{n}\t{it.source}\t{it.published.date()}\t{clean(it.feed)}\t{clean(','.join(it.tags))}\t"
                    f"{'thin' if thin else ''}\t{clean(it.title)}")
    (out / "titles.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if transcript:
        log.info("Transcripts found for %d videos", found)
    return len(items)


class _Reply:
    """Stands in for the LLM: the page JSON an agent already wrote."""

    def __init__(self, obj: dict):
        self.obj = obj

    def chat_json(self, model, system, user):
        return self.obj


def _item(d: dict) -> Item:
    return Item(id=d["id"], source=d["source"], url=d["url"], title=d["title"], text=d["text"],
                published=datetime.fromisoformat(d["published"]), feed=d["feed"], tags=tuple(d["tags"]))


def import_pages(folder: Path, dav, state, page_prompt: str) -> dict[str, int]:
    """Writes every topic page that has an agent output; safe to re-run after each batch."""
    assignment = json.loads((folder / "assignment.json").read_text(encoding="utf-8"))
    pages: dict[str, tuple[str, list]] = {}  # filename stem -> (title, items)
    needs: dict[str, set[str]] = {}  # item id -> filename stems
    nums: dict[str, list[str]] = {}  # filename stem -> item numbers, for todo.txt
    for n, titles in assignment.items():
        path = folder / "items" / f"{n}.json"
        if not (n.isdigit() and path.exists() and isinstance(titles, list)):
            continue
        it = _item(json.loads(path.read_text(encoding="utf-8")))
        if state.is_processed(it.id):
            continue
        for t in [t for t in titles if isinstance(t, str) and t.strip()][:2]:
            stem = safe_filename(t)
            pages.setdefault(stem, (t, []))[1].append(it)
            needs.setdefault(it.id, set()).add(stem)
            nums.setdefault(stem, []).append(n)
    counts = dict.fromkeys(("written", "flagged", "failed", "missing", "invalid", "items_processed"), 0)
    written, todo = set(), []
    for stem, (title, items) in pages.items():
        out = folder / "out" / f"{stem}.json"
        if not out.exists():
            counts["missing"] += 1
            todo.append((title, stem))
            continue
        try:
            obj = json.loads(out.read_text(encoding="utf-8"))
            valid = isinstance(obj, dict) and isinstance(obj.get("summary"), str) and isinstance(obj.get("recent"), str)
        except ValueError:
            valid = False
        if not valid:
            log.warning("invalid page JSON %s, skipping", out)
            counts["invalid"] += 1
            todo.append((title, stem))
            continue
        result = write_topic_page(stem, title, items, dav, state, _Reply(obj), "agent", page_prompt)
        counts[result] += 1
        if result == "written":
            written.add(stem)
    all_items = list({it.id: it for _, items in pages.values() for it in items}.values())
    mark_kept(all_items, needs, written, state)
    counts["items_processed"] = sum(state.is_processed(it.id) for it in all_items)
    # one line per page still to write, biggest first: title, item count, output file, item numbers
    todo.sort(key=lambda x: -len(nums[x[1]]))
    (folder / "todo.txt").write_text(
        "".join(f"{t}\t{len(nums[stem])}\t{stem}.json\t{','.join(nums[stem])}\n" for t, stem in todo),
        encoding="utf-8")
    return counts
