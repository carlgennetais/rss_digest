import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

import markdown

A = "http://www.w3.org/2005/Atom"
FEED_ID = "urn:rss-digest:digest"
MAX_ENTRIES = 30
WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

ET.register_namespace("", A)


def _el(parent, tag: str, text: str | None = None, **attrs) -> ET.Element:
    e = ET.SubElement(parent, f"{{{A}}}{tag}", attrs)
    e.text = text
    return e


def update_feed(existing: str | None, day: date, md: str, now: datetime) -> str:
    """Returns the Atom feed with this day's digest added (replacing a same-day entry), newest first."""
    entry_id = f"{FEED_ID}:{day.isoformat()}"
    old = [e for e in ET.fromstring(existing).findall(f"{{{A}}}entry")
           if e.findtext(f"{{{A}}}id") != entry_id] if existing else []
    feed = ET.Element(f"{{{A}}}feed")
    _el(feed, "title", "RSS digest")
    _el(feed, "id", FEED_ID)
    _el(feed, "updated", now.isoformat())
    _el(_el(feed, "author"), "name", "rss-digest")
    entry = _el(feed, "entry")
    _el(entry, "title", f"Digest {day.isoformat()}")
    _el(entry, "id", entry_id)
    _el(entry, "updated", now.isoformat())
    # Obsidian [[Page|alias]] links mean nothing in a feed reader: keep the text only.
    text = WIKILINK.sub(lambda m: m.group(2) or m.group(1), md)
    _el(entry, "content", markdown.markdown(text), type="html")
    feed.extend(sorted(old, key=lambda e: e.findtext(f"{{{A}}}id"), reverse=True)[:MAX_ENTRIES - 1])
    return ET.tostring(feed, encoding="unicode", xml_declaration=True)
