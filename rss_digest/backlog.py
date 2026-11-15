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
from pathlib import Path

from .fetch import THIN, is_video

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
            subprocess.run(["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
                            "--sub-langs", "en.*,fr.*", "--sub-format", "vtt", "-o", f"{tmp}/t", url],
                           capture_output=True, timeout=60, check=True)
        except (subprocess.SubprocessError, OSError):
            return None
        vtts = sorted(Path(tmp).glob("*.vtt"))
        return vtt_to_text(vtts[0].read_text(encoding="utf-8", errors="replace")) if vtts else None


def export(items, out: Path, transcript=None) -> int:
    """Writes out/items/<n>.json and out/titles.tsv; returns the number of items."""
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
