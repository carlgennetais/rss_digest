import dataclasses

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


def test_sibling_page_not_rewritten_with_known_items(dav, state, edited):
    a = item("a")
    run([a], dav, state, FakeLLM(json_replies=[{"1": ["K8s", "NEW: B"]}, W]))
    b_text = dav.files["Topics/B.md"]
    llm = FakeLLM(json_replies=[{"1": ["K8s", "B"]}])
    assert run([a], dav, state, llm) == ["K8s"]
    assert len(llm.calls) == 1 and dav.files["Topics/B.md"] == b_text and not state.is_processed(a.id)


def test_writer_prompt_carries_json_contract(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, W])
    update_vault([item("a")], dav, state, llm, "c", "w", "user prompt")
    system = llm.calls[1][2]
    assert system.startswith("user prompt") and all(f'"{k}"' in system for k in ("description", "summary", "recent"))


def test_contract_reply_parses():
    from rss_digest.llm import _parse_json
    reply = 'Here:\n{"description": "d", "summary": "See [x](https://e.com) now", "recent": "- 2026-11-14: [a](u)"}'
    assert _parse_json(reply)["recent"] == "- 2026-11-14: [a](u)"


def test_auth_error_is_not_swallowed(dav, state):
    from rss_digest.llm import LLMAuthError

    class AuthFail(FakeLLM):
        def chat_json(self, *a):
            raise LLMAuthError("401")
    with pytest.raises(LLMAuthError):
        run([item("a")], dav, state, AuthFail())


def test_empty_url_or_title_is_not_a_dedup_key(dav, state):
    from dataclasses import replace
    no_url = [replace(item(t), url="") for t in ("a", "b")]
    no_title = [item("", url=f"https://ex.com/{t}", id=f"freshrss:{t}") for t in ("c", "d")]
    assert select_new_kept(no_url + no_title, state) == no_url + no_title
    run([no_url[0], no_title[0]], dav, state, FakeLLM(json_replies=[{"1": ["NEW: T"], "2": ["T"]}, W]))
    assert state.is_processed(no_url[0].id) and state.is_processed(no_title[0].id)
    assert not state.has_key("u:https:///") and not state.has_key("t:")
    assert select_new_kept([no_url[1], no_title[1]], state) == [no_url[1], no_title[1]]


def test_spelling_variant_of_own_page_is_updated_not_flagged(dav, state):
    run([item("a")], dav, state, FakeLLM(json_replies=[{"1": ["NEW: CI/CD"]}, W]))
    llm = FakeLLM(json_replies=[{"1": ["NEW: CI-CD"]}, {"summary": "new", "recent": "- r"}])
    assert run([item("b")], dav, state, llm) == []
    assert parse_page(dav.files["Topics/CI-CD.md"]).summary == "new" and state.is_processed(item("b").id)


@pytest.mark.parametrize("op", ["read", "write"])
def test_dav_error_on_one_page_skips_only_that_page(state, op):
    import httpx
    from conftest import FakeDav

    class Locked(FakeDav):
        def _check(self, path):
            if path == "Topics/A.md":
                req = httpx.Request("PUT", "http://dav/" + path)
                raise httpx.HTTPStatusError("423", request=req, response=httpx.Response(423, request=req))

        def read(self, path):
            if op == "read":
                self._check(path)
            return super().read(path)

        def write(self, path, text):
            self._check(path)
            super().write(path, text)
    dav = Locked()
    llm = FakeLLM(json_replies=[{"1": ["NEW: A"], "2": ["NEW: B"]}, W, W])
    assert run([item("x"), item("y")], dav, state, llm) == []
    assert list(dav.files) == ["Topics/B.md"]
    assert not state.is_processed(item("x").id) and state.is_processed(item("y").id)


def test_assignment_prompt_includes_wallabag_tags(dav, state):
    tagged = dataclasses.replace(item("a"), tags=("kubernetes", "security"))
    llm = FakeLLM(json_replies=[{"1": ["NEW: K8s"], "2": ["NEW: K8s"]}, W])
    run([tagged, item("b")], dav, state, llm)
    user = llm.calls[0][3]
    assert "1. a (Ex Blog) [tags: kubernetes, security]" in user and "2. b (Ex Blog)\n" in user + "\n"


def test_dedup_keeps_the_copy_with_most_text(state):
    star = item("Same", url="https://ex.com/p", id="freshrss:1", text="")
    saved = item("Same", url="https://ex.com/p?utm_source=x", id="wallabag:9", text="full article " * 100)
    assert [i.id for i in select_new_kept([star, saved], state)] == ["wallabag:9"]


def test_writer_gets_full_article_text(dav, state):
    long = item("a", text="w" * 5000 + "END")
    llm = FakeLLM(json_replies=[{"1": ["NEW: T"]}, {"description": "d", "summary": "s", "recent": ""}])
    run([long], dav, state, llm)
    assert "w" * 5000 + "END" in llm.calls[1][3]


def test_writer_links_known_topics_drops_unknown_and_sets_kind(dav, state):
    run([item("x")], dav, state, FakeLLM(json_replies=[{"1": ["NEW: DuckDB"]}, W]))  # DuckDB.md now exists
    llm = FakeLLM(json_replies=[{"1": ["NEW: dbt"]}, {
        "description": "d", "kind": "entity", "recent": "- r",
        "summary": "Often paired with [DuckDB](<DuckDB.md>), see [Ghost](<Ghost.md>) and [docs](https://docs.getdbt.com)."}])
    run([item("y")], dav, state, llm)
    assert "DuckDB.md" in llm.calls[1][3]  # the writer is told which topics exist
    page = parse_page(dav.files["Topics/dbt.md"])
    assert "[DuckDB](<DuckDB.md>)" in page.summary and "[docs](https://docs.getdbt.com)" in page.summary
    assert "Ghost" in page.summary and "Ghost.md" not in page.summary  # unknown page: text kept, link dropped
    assert page.front["kind"] == "entity"


def test_kind_must_be_entity_or_concept(dav, state):
    run([item("x")], dav, state, FakeLLM(json_replies=[{"1": ["NEW: T"]}, dict(W, kind="banana")]))
    assert "kind" not in parse_page(dav.files["Topics/T.md"]).front
