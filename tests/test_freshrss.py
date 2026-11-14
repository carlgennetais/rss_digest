from datetime import UTC, datetime

import httpx
import pytest

from conftest import Recorder
from rss_digest.config import FreshRSSConf
from rss_digest.freshrss import FreshRSS

conf = FreshRSSConf(url="https://rss.example/", user="u", api_password="p")


def entry(n, **kw):
    return {
        "id": f"tag:google.com,2005:reader/item/{n}",
        "title": f"T{n}",
        "published": 1794700000 + n,
        "canonical": [{"href": f"https://ex.com/{'a' if n == 1 else 'b'}"}],
        "summary": {"content": "<p>Hello <b>world</b></p>"},
        "origin": {"title": "Ex Blog"},
        **kw,
    }


def handler(request):
    if request.url.path.endswith("/ClientLogin"):
        return httpx.Response(200, text="SID=x\nLSID=y\nAuth=TOKEN\n")
    if "c" not in request.url.params:
        return httpx.Response(200, json={"items": [entry(1)], "continuation": "c1"})
    return httpx.Response(200, json={"items": [entry(2)]})


def test_reading_list_paginates_and_maps():
    rec = Recorder(handler)
    since = datetime(2026, 11, 13, tzinfo=UTC)
    items = FreshRSS(conf, client=rec.client).reading_list(since=since)
    assert [i.id for i in items] == [
        "freshrss:tag:google.com,2005:reader/item/1",
        "freshrss:tag:google.com,2005:reader/item/2",
    ]
    assert items[0].url == "https://ex.com/a" and items[0].feed == "Ex Blog"
    assert "<" not in items[0].text and "Hello" in items[0].text
    assert items[0].source == "freshrss" and items[0].published.tzinfo is not None
    assert rec.requests[0].url.path == "/api/greader.php/accounts/ClientLogin"
    assert rec.requests[1].url.params["ot"] == str(int(since.timestamp()))
    assert rec.requests[1].headers["Authorization"] == "GoogleLogin auth=TOKEN"
    assert rec.requests[2].url.params["c"] == "c1"


def test_starred_falls_back_to_alternate_and_skips_ot():
    e = entry(1)
    del e["canonical"]
    e["alternate"] = [{"href": "https://ex.com/alt"}]

    def h(request):
        if request.url.path.endswith("/ClientLogin"):
            return httpx.Response(200, text="Auth=TOKEN\n")
        return httpx.Response(200, json={"items": [e]})

    rec = Recorder(h)
    items = FreshRSS(conf, client=rec.client).starred()
    assert items[0].url == "https://ex.com/alt"
    assert rec.requests[1].url.path.endswith("/state/com.google/starred")
    assert "ot" not in rec.requests[1].url.params


def test_freshrss_login_failure_raises():
    rec = Recorder(lambda r: httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        FreshRSS(conf, client=rec.client).starred()
