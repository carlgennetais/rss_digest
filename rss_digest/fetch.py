"""Reads an article's web page when the text stored by FreshRSS or Wallabag is thin or empty."""
import dataclasses
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .models import Item
from .textutil import html_to_text

log = logging.getLogger(__name__)
THIN = 1000  # characters: below this, the stored text is not enough to write from
MAX_BYTES = 2_000_000
MIN_PAGE_TEXT = 500  # less than this is a bot-check, error or login page, not an article
USER_AGENT = "rss-digest (+personal reader)"


def is_video(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:  # malformed URL, e.g. an unclosed IPv6 bracket
        return False
    return "youtube.com" in host or "youtu.be" in host


def page_text(url: str, client: httpx.Client | None = None) -> str | None:
    """The main text of the page at `url`, or None if it can't be read as an article."""
    client = client or httpx.Client()
    try:
        with client.stream("GET", url, follow_redirects=True, timeout=15, headers={"User-Agent": USER_AGENT}) as r:
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("text/html"):
                return None
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    return None
            html = body.decode(r.encoding or "utf-8", errors="replace")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, LookupError):  # LookupError: bad charset
        return None
    text = html_to_text(html, main_only=True)
    return text if len(text) >= MIN_PAGE_TEXT else None


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


def enrich(items: list[Item], fetch=None, transcript=None) -> list[Item]:
    """Replaces thin text with the article's page text, or a video's subtitles, where they can be read."""
    fetch = fetch or page_text  # resolved per call, so tests can stub the module attribute
    transcript = transcript or yt_transcript
    out, tried, improved = [], 0, 0
    total = sum(1 for it in items if len(it.text) < THIN and it.url)
    for it in items:
        if len(it.text) < THIN and it.url:
            tried += 1
            if tried % 25 == 0:
                log.info("Reading pages and transcripts: %d/%d (%d improved)", tried, total, improved)
            try:  # one odd URL or video must never stop a run
                text = transcript(it.url) if is_video(it.url) else fetch(it.url)
            except Exception:
                log.warning("reading %s failed", it.url, exc_info=True)
                text = None
            if text and len(text) > len(it.text):
                it = dataclasses.replace(it, text=text)
                improved += 1
        out.append(it)
    if tried:
        log.info("Read %d pages or transcripts for thin items: %d improved, %d failed", tried, improved, tried - improved)
    return out
