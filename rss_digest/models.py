from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Item:
    id: str  # "freshrss:<id>" or "wallabag:<id>"
    source: str
    url: str
    title: str
    text: str  # plain text, no images
    published: datetime  # tz-aware UTC
    feed: str
    tags: tuple[str, ...] = ()
    read: bool = False  # FreshRSS only: the reader has seen it
