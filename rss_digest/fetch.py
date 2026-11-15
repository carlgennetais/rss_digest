"""Reads an article's web page when the text stored by FreshRSS or Wallabag is thin or empty."""
import dataclasses
import logging
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
    host = urlparse(url).netloc.lower()
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
    except httpx.HTTPError:
        return None
    text = html_to_text(html, main_only=True)
    return text if len(text) >= MIN_PAGE_TEXT else None


def enrich(items: list[Item], fetch=None) -> list[Item]:
    """Replaces thin text with the article's page text where the page can be read."""
    fetch = fetch or page_text  # resolved per call, so tests can stub the module attribute
    out, tried, improved = [], 0, 0
    for it in items:
        if len(it.text) < THIN and it.url and not is_video(it.url):
            tried += 1
            text = fetch(it.url)
            if text and len(text) > len(it.text):
                it = dataclasses.replace(it, text=text)
                improved += 1
        out.append(it)
    if tried:
        log.info("Read %d web pages for thin items: %d improved, %d failed", tried, improved, tried - improved)
    return out
