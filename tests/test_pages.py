import pytest

from rss_digest.pages import PageParseError, ai_hash, is_topic_page, new_page, parse_page, render_page


def test_roundtrip_preserves_my_notes_bytes():
    tail = "## My notes\n\nFrom training 2026-10:\n![](../_attachments/slide.png)\n\n  trailing spaces  \n"
    p = new_page("Kubernetes"); p.my_notes_tail = tail; p.summary = "S"; p.front["sources"] = [{"url": "https://a", "title": "A", "id": "wallabag:1", "date": "2026-11-01"}]
    out = render_page(p)
    assert out.endswith(tail) and parse_page(out).my_notes_tail == tail
    assert out.index("## Summary") < out.index("## Recent changes") < out.index("## Sources") < out.index("## My notes")
    assert "- [A](https://a) — 2026-11-01" in out


def test_parse_rejects_missing_or_duplicate_my_notes():
    with pytest.raises(PageParseError): parse_page(render_page(new_page("X")).replace("## My notes", "## Notes"))
    with pytest.raises(PageParseError): parse_page(render_page(new_page("X")) + "\n## My notes\n")


def test_parse_accepts_block_style_yaml_from_obsidian():
    text = "---\ntype: topic\ntitle: X\ndescription: d\nupdated: 2026-11-14\nsources:\n  - url: https://a\n    title: A\n    id: freshrss:1\n    date: 2026-11-01\n---\n## Summary\ns\n## Recent changes\n## Sources\n## My notes\n"
    p = parse_page(text)
    assert p.front["sources"][0]["id"] == "freshrss:1"
    assert p.front["updated"] == "2026-11-14" and p.front["sources"][0]["date"] == "2026-11-01"


def test_hand_made_page_is_not_topic():
    assert not is_topic_page("# My own page\n") and not is_topic_page("---\ntags: [x]\n---\nhi")
    assert not is_topic_page("---\n: [bad\n---\n")


def test_ai_hash_ignores_my_notes():
    a = render_page(new_page("X")); assert ai_hash(a) == ai_hash(a + "my scribbles\n") != ai_hash(a.replace("## Summary\n", "## Summary\nedited\n"))


def test_render_parse_render_stable_and_hash_stable():
    p = new_page("X"); p.summary = "\n\nsum\n\n"; p.recent = "- a\n- b"
    out = render_page(p)
    again = render_page(parse_page(out))
    assert again == out and ai_hash(again) == ai_hash(out)
    obs = parse_page(out.replace("## My notes\n", "## My notes  \r\nmine\r\n"))
    assert obs.my_notes_tail == "## My notes  \r\nmine\r\n"


@pytest.mark.parametrize("bad", [
    "no frontmatter\n",
    "---\ntype: topic\n## Summary\n",
    "---\n- a\n---\n",
    "---\ntype: note\n---\n## Summary\n## Recent changes\n## Sources\n## My notes\n",
    "---\ntype: topic\n---\n## Recent changes\n## Summary\n## Sources\n## My notes\n",
    "---\ntype: topic\n---\n## Summary\n## Sources\n## My notes\n",
])
def test_parse_errors(bad):
    with pytest.raises(PageParseError):
        parse_page(bad)


def test_missing_sources_key_is_empty_list():
    p = parse_page("---\ntype: topic\n---\n## Summary\n## Recent changes\n## Sources\n## My notes\n")
    assert p.front["sources"] == []
