import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta

from rss_digest.feed import update_feed

A = "{http://www.w3.org/2005/Atom}"
NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)


def entries(xml):
    return ET.fromstring(xml).findall(f"{A}entry")


def test_new_feed_has_html_entry_without_wikilinks():
    xml = update_feed(None, date(2026, 11, 15), "# Digest\n## K8s\n- [a](https://ex.com/a): s\n\n[[Kubernetes]] and [[CI/CD|ci]]", NOW)
    (e,) = entries(xml)
    assert e.find(f"{A}title").text == "Digest 2026-11-15"
    html = e.find(f"{A}content").text
    assert '<a href="https://ex.com/a">a</a>' in html and "[[" not in html and "Kubernetes and ci" in html
    assert ET.fromstring(xml).find(f"{A}updated").text == NOW.isoformat()


def test_newest_first_capped_and_same_day_replaced():
    xml = None
    for d in range(35):
        xml = update_feed(xml, date(2026, 10, 1) + timedelta(days=d), f"day {d}", NOW)
    xml = update_feed(xml, date(2026, 11, 4), "rerun", NOW)
    es = entries(xml)
    assert len(es) == 30
    assert es[0].find(f"{A}title").text == "Digest 2026-11-04" and "rerun" in es[0].find(f"{A}content").text
    assert len({e.find(f"{A}id").text for e in es}) == 30
