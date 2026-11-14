import pytest

from rss_digest.textutil import html_to_text, normalize_url, safe_filename, strip_md_images, title_key


def test_normalize_url():
    assert normalize_url("http://WWW.Example.com/a/?utm_source=x&b=2&a=1&fbclid=z#frag") == "https://example.com/a?a=1&b=2"
    assert normalize_url("https://example.com/a/") == normalize_url("https://example.com/a")
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_title_key():
    assert title_key("  Kubernetes 1.32: What's New! ") == title_key("kubernetes 1.32 — whats new")
    assert title_key("Sécurité") != title_key("Other")


def test_html_to_text_drops_images_and_scripts():
    t = html_to_text('<p>Hello <b>world</b></p><img src="x.png"><figure><img src="y"><figcaption>cap</figcaption></figure><script>evil()</script><p>Bye</p>')
    assert "Hello world" in t and "Bye" in t
    assert "x.png" not in t and "evil" not in t and "<" not in t


def test_html_to_text_unescapes_entities():
    assert html_to_text("<p>a &amp; b</p>") == "a & b"


def test_strip_md_images():
    assert strip_md_images("a ![alt](http://x/y.png) b ![[pic.png]] c <img src='z'>") == "a  b  c "


@pytest.mark.parametrize("title", ["CI/CD: pipelines?", 'a*b"c<d>e|f#g^h[i]j\\k', "Sécurité réseau", "...", "", "x" * 300])
def test_safe_filename(title):
    f = safe_filename(title)
    assert f and not set('/\\:*?"<>|#^[]') & set(f) and not f.startswith(".") and len(f) <= 100
    assert f == safe_filename(title)


def test_safe_filename_keeps_unicode():
    assert safe_filename("Sécurité réseau") == "Sécurité réseau"
    assert safe_filename("CI/CD: pipelines?") == "CI-CD - pipelines"
    assert safe_filename("...") == "Untitled"
