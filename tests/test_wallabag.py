from datetime import UTC, datetime

import httpx

from conftest import Recorder
from rss_digest.config import WallabagConf
from rss_digest.wallabag import Wallabag

conf = WallabagConf(url="https://wb.example/", client_id="i", client_secret="s", username="u", password="p")


def item(n, tags=()):
    return {
        "id": n,
        "url": f"https://ex.com/{n}",
        "title": f"T{n}",
        "content": "<p>Body <img src='x'></p>",
        "created_at": "2026-11-01T10:00:00+0100",
        "tags": [{"label": t} for t in tags],
    }


def handler(request):
    if request.url.path == "/oauth/v2/token":
        return httpx.Response(200, json={"access_token": "AT"})
    page = int(request.url.params["page"])
    items = [item(42, ("k8s", "security")), item(43)] if page == 1 else [item(44)]
    return httpx.Response(200, json={"page": page, "pages": 2, "_embedded": {"items": items}})


def test_entries_pages_and_tags():
    rec = Recorder(handler)
    items = Wallabag(conf, client=rec.client).entries(since=None)
    assert len(items) == 3
    assert items[0].tags == ("k8s", "security") and items[0].id == "wallabag:42"
    assert items[0].feed == "wallabag" and items[0].source == "wallabag"
    assert items[0].published == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert items[0].text == "Body"
    assert "since" not in rec.requests[1].url.params
    assert rec.requests[1].headers["Authorization"] == "Bearer AT"
    assert b"grant_type=password" in rec.requests[0].content


def test_entries_since():
    rec = Recorder(handler)
    since = datetime(2026, 11, 13, tzinfo=UTC)
    Wallabag(conf, client=rec.client).entries(since=since)
    assert rec.requests[1].url.params["since"] == str(int(since.timestamp()))
