"""Splits a newsletter issue into the articles it links, so each one is ranked, deduped and summarised alone."""
import re
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import urlsplit

from rss_digest.models import Item
from rss_digest.textutil import _TextExtractor

BLOCK = _TextExtractor.BLOCK | {"td", "table", "ul", "ol"}
JUNK = re.compile(r"sponsor|unsubscribe|referral|\*\s*$", re.IGNORECASE)
PAST = re.compile(r"\blast (week|issue)['’]?s\b.*\blinks\b", re.IGNORECASE)  # "Last Issue's Top Links": repeats of old issues
MIN_WORDS = 4  # shorter link texts are "here", "DETAILS →", author names
MIN_BLURB = 40  # buttons, footers and "last issue's top links" have little or no text after them
MIN_ARTICLES = 3  # fewer: not a link roundup (an invite, a single essay), keep the issue whole


class _Links(HTMLParser):
    """Text as one stream; each link with its span in it and whether it starts its paragraph or list item."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str, bool, int, int]] = []  # href, title, starts its block, start, end
        self.anchor: tuple[str, bool, int] | None = None
        self.line = ""  # text since the last block boundary

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK:
            self.line = ""
        elif tag == "a":
            self.anchor = (dict(attrs).get("href") or "", not re.sub(r"[\W\d_]+", "", self.line), len(self.text))

    def handle_endtag(self, tag):
        if tag in BLOCK:
            self.line = ""
        elif tag == "a" and self.anchor:
            href, starts, start = self.anchor
            self.links.append((href, " ".join("".join(self.text[start:]).split()), starts, start, len(self.text)))
            self.anchor = None

    def handle_data(self, data):
        self.text.append(data)
        self.line += data


def _article(href: str, title: str, starts: bool) -> bool:
    return (starts and len(title.split()) >= MIN_WORDS and not JUNK.search(title)
            and href.startswith("http") and urlsplit(href).netloc != "kill-the-newsletter.com")


def split(item: Item, html: str, resolve) -> list[Item]:
    p = _Links()
    p.feed(html or "")
    p.close()
    cut = next((i for i, t in enumerate(p.text) if len(t.strip()) < 60 and PAST.search(t)), len(p.text))
    stops = [start for _, _, starts, start, _ in p.links if starts and start < cut] + [cut]  # a blurb ends at the next block's link
    found = []
    for href, title, starts, _, end in p.links:
        if end > cut or not _article(href, title, starts):
            continue
        stop = next(s for s in stops if s >= end)
        blurb = " ".join("".join(p.text[end:stop]).split())
        if len(blurb) >= MIN_BLURB:
            found.append((href, title, blurb[:1500]))
    if len(found) < MIN_ARTICLES:
        return [item]
    return [replace(item, id=f"{item.id}#{n}", url=resolve(href), title=title, text=blurb)
            for n, (href, title, blurb) in enumerate(found)]
