import httpx

from conftest import item
from rss_digest.fetch import enrich, is_video, page_text

ARTICLE = "<html><body><nav>Menu</nav><article>" + "<p>Real article sentence here.</p>" * 40 + "</article></body></html>"


def client():
    def handler(request):
        path = request.url.path
        if path == "/ok":
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=ARTICLE)
        if path == "/404":
            return httpx.Response(404, text="nope")
        if path == "/slow":
            raise httpx.ReadTimeout("slow", request=request)
        if path == "/f.pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        if path == "/cf":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html><head><title>Just a moment...</title></head><body>Checking</body></html>")
        if path == "/big":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>x</p>" * 400_000)
        raise AssertionError(path)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_page_text_extracts_main_content():
    t = page_text("https://ex.com/ok", client=client())
    assert t.startswith("Real article sentence") and "Menu" not in t


def test_page_text_returns_none_on_dead_or_unusable_pages():
    for path in ("/404", "/slow", "/f.pdf", "/cf", "/big"):
        assert page_text(f"https://ex.com{path}", client=client()) is None, path


def test_is_video():
    assert is_video("https://www.youtube.com/watch?v=a") and is_video("https://youtu.be/a")
    assert not is_video("https://ex.com/youtube-tips")


def test_enrich_only_thin_non_video_and_keeps_text_on_failure():
    full = item("f", text="x" * 2000)
    thin, video, dead = item("t", url="https://ex.com/t"), item("v", url="https://youtu.be/a"), item("d", url="https://ex.com/d")
    calls = []

    def fetch(url):
        calls.append(url)
        return "page " * 200 if url.endswith("/t") else None

    out = enrich([full, thin, video, dead], fetch=fetch)
    assert calls == ["https://ex.com/t", "https://ex.com/d"]
    assert out[1].text.startswith("page") and out[0] == full and out[2] == video and out[3] == dead


def test_enrich_survives_a_fetch_that_raises():
    def boom(url):
        raise httpx.InvalidURL("bad")  # not an httpx.HTTPError subclass

    out = enrich([item("t", url="http://[::1")], fetch=boom)
    assert out[0].text == ""


def test_page_text_handles_invalid_url():
    assert page_text("http://[::1") is None


def test_is_video_on_malformed_url():
    assert is_video("http://[::1") is False
