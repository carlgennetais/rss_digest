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


def test_reading_list_drops_entries_crawled_before_since():
    since = datetime(2026, 11, 13, tzinfo=UTC)
    ms = int(since.timestamp() * 1000)
    rec = Recorder(lambda r: httpx.Response(200, text="SID=x\nLSID=y\nAuth=TOKEN\n") if r.url.path.endswith("/ClientLogin")
                   else httpx.Response(200, json={"items": [entry(1, crawlTimeMsec=str(ms - 1)), entry(2, crawlTimeMsec=str(ms)), entry(3)]}))
    assert [i.title for i in FreshRSS(conf, client=rec.client).reading_list(since=since)] == ["T2", "T3"]


def test_reading_list_splits_newsletter_categories_only():
    from test_newsletter import ISSUE
    labelled = entry(1, categories=["user/-/label/news"], summary={"content": ISSUE})
    rec = Recorder(lambda r: httpx.Response(200, text="SID=x\nLSID=y\nAuth=TOKEN\n") if r.url.path.endswith("/ClientLogin")
                   else httpx.Response(302, headers={"location": "https://real.example" + r.url.path}) if r.url.host == "t.example"
                   else httpx.Response(200, json={"items": [labelled, entry(2, summary={"content": ISSUE})]}) if r.url.host == "rss.example"
                   else httpx.Response(200))
    fr = FreshRSS(FreshRSSConf(conf.url, "u", "p", newsletter_categories=["news"]), client=rec.client)
    items = fr.reading_list(since=datetime(2026, 11, 13, tzinfo=UTC))
    assert [i.url for i in items] == ["https://real.example/1", "https://real.example/2", "https://real.example/3", "https://ex.com/b"]
    rec.requests.clear()
    assert len(fr.starred()) == 2 and all(r.url.host == "rss.example" for r in rec.requests)  # the vault gets issues whole


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


def test_read_state_comes_from_categories():
    def h(request):
        if request.url.path.endswith("/ClientLogin"):
            return httpx.Response(200, text="Auth=TOKEN\n")
        return httpx.Response(200, json={"items": [
            entry(1, categories=["user/-/state/com.google/reading-list", "user/-/state/com.google/read"]),
            entry(2, categories=["user/-/state/com.google/reading-list"]),
            entry(3),
        ]})

    items = FreshRSS(conf, client=Recorder(h).client).reading_list(since=datetime(2026, 11, 1, tzinfo=UTC))
    assert [i.read for i in items] == [True, False, False]
