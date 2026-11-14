import pytest
from conftest import FakeLLM, item

from rss_digest.llm import BudgetExceeded
from rss_digest.pages import parse_page
from rss_digest.vault import TopicRef, list_topics, profile_text, select_new_kept, update_vault

W = {"description": "d", "summary": "s", "recent": "- r"}


def run(items, dav, state, llm):
    return update_vault(items, dav, state, llm, "c", "w", "")


def test_select_new_kept_dedups_star_and_wallabag(state):
    a = item("Same", url="https://ex.com/p?utm_source=x", id="freshrss:1")
    b = item("Same", url="https://ex.com/p", id="wallabag:9")
    assert [i.id for i in select_new_kept([a, b], state)] == ["freshrss:1"]


def test_select_new_kept_skips_known(state):
    state.mark_processed("freshrss:a")
    state.add_key("t:other")
    got = select_new_kept([item("a", id="freshrss:a"), item("Other"), item("fresh")], state)
    assert [i.title for i in got] == ["fresh"]


def test_new_topic_created_and_items_processed(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: CI/CD"]},
                                {"description": "d", "summary": "s ![](x.png)", "recent": "- 2026-11-14: x"}])
    assert run([item("a")], dav, state, llm) == []
    page = parse_page(dav.files["Topics/CI-CD.md"])
    assert page.front["title"] == "CI/CD" and "![" not in page.summary and state.is_processed(item("a").id)
    assert page.front["sources"][0]["url"] == item("a").url
    assert state.has_key("t:a") and state.has_key("u:https://ex.com/a")


@pytest.fixture
def edited(dav, state):
    run([item("seed")], dav, state, FakeLLM(json_replies=[{"1": ["NEW: K8s"]}, W]))
    dav.files["Topics/K8s.md"] = dav.files["Topics/K8s.md"].replace("## Summary\ns", "## Summary\nCarl was here")
    return dav.files["Topics/K8s.md"]


def test_edited_page_is_flagged_and_untouched(dav, state, edited):
    llm = FakeLLM(json_replies=[{"1": ["k8s"]}])
    assert run([item("a")], dav, state, llm) == ["K8s"]
    assert dav.files["Topics/K8s.md"] == edited and not state.is_processed(item("a").id)


def test_hand_made_page_never_written(dav, state):
    dav.files["Topics/Mine.md"] = "# my page"
    run([item("a")], dav, state, FakeLLM(json_replies=[{"1": ["Mine"]}]))
    assert dav.files["Topics/Mine.md"] == "# my page"


def test_my_notes_preserved_on_update(dav, state):
    run([item("seed")], dav, state, FakeLLM(json_replies=[{"1": ["NEW: T"]}, W]))
    notes = "## My notes\nmine é\n\n\n- keep   \n"
    text = dav.files["Topics/T.md"]
    dav.files["Topics/T.md"] = text[:text.index("## My notes")] + notes
    from rss_digest.pages import ai_hash
    state.set_page_hash("T", ai_hash(dav.files["Topics/T.md"]))
    llm = FakeLLM(json_replies=[{"1": ["T"]}, {"summary": "new", "recent": "x"}])
    assert run([item("b")], dav, state, llm) == []
    out = dav.files["Topics/T.md"]
    assert out.endswith(notes) and parse_page(out).summary == "new"
    assert parse_page(out).front["description"] == "d" and len(parse_page(out).front["sources"]) == 2
    assert notes in llm.calls[1][3]


def test_budget_stop_leaves_unwritten_items_unprocessed(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: A"], "2": ["NEW: B"]}, W], raise_after=2)
    with pytest.raises(BudgetExceeded):
        run([item("x"), item("y")], dav, state, llm)
    assert state.is_processed(item("x").id) and not state.is_processed(item("y").id)


def test_bad_assignment_json_leaves_items_unprocessed(dav, state):
    assert run([item("a")], dav, state, FakeLLM(json_errors=1)) == [] and not state.is_processed(item("a").id)


def test_assignment_forms(dav, state):
    run([item("seed")], dav, state, FakeLLM(json_replies=[{"1": "NEW: Rust"}, W]))
    llm = FakeLLM(json_replies=[{"1": ["rust", "nEw: Go", "Third"], "2": [" ", ""], "9": ["Rust"]}, W, W])
    run([item("a"), item("b")], dav, state, llm)
    assert sorted(dav.files) == ["Topics/Go.md", "Topics/Rust.md"]
    assert state.is_processed(item("a").id) and not state.is_processed(item("b").id)


def test_titles_with_same_file_are_one_page(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: A/B"], "2": ["NEW: A-B"]}, W])
    run([item("x"), item("y")], dav, state, llm)
    assert len(llm.calls) == 2 and list(dav.files) == ["Topics/A-B.md"]
    assert state.is_processed(item("x").id) and state.is_processed(item("y").id)


def test_item_needs_all_pages(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: A", "NEW: B"]}, W, {"summary": 3, "recent": ""}])
    run([item("x")], dav, state, llm)
    assert list(dav.files) == ["Topics/A.md"] and not state.is_processed(item("x").id)


def test_list_topics_and_profile(dav):
    dav.files["Topics/One.md"] = "---\ntype: topic\ntitle: Uno\ndescription: first\n---\n## Summary\n\n## Recent changes\n\n## Sources\n\n## My notes\n"
    dav.files["Topics/Bad.md"] = "# nope"
    topics = list_topics(dav)
    assert topics == [TopicRef("One.md", "Uno", "first")]
    assert profile_text(topics) == "Uno: first"
