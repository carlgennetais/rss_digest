from datetime import date

from rss_digest.digest import build_digest
from conftest import FakeLLM, item

D = date(2026, 11, 14)


def test_empty_day_makes_no_llm_calls(interests):
    llm = FakeLLM()
    md = build_digest(D, [], interests, "", llm, "c", "w", "", 15, 10, [], stats="Fetched 3")
    assert "No new articles" in md and "Fetched 3" in md and llm.calls == []


def test_digest_has_no_images_and_lists_flags(interests):
    llm = FakeLLM(json_replies=[{"1": 9, "2": 5}], text_replies=["## K8s\n![x](http://i/x.png)\n- [a](https://ex.com/a): summary"])
    md = build_digest(D, [item("a"), item("b")], interests, "", llm, "c", "w", "", top_n=1, also_notable=10, flagged_pages=["Kubernetes"], stats="Fetched 2")
    assert md.startswith("# Digest 2026-11-14") and "![" not in md and "- [b](" in md
    assert "[[Kubernetes]]" in md and "Fetched 2" in md and "## Also notable" in md


def test_images_stripped_from_llm_input(interests):
    llm = FakeLLM(json_replies=[{"1": 9}], text_replies=["body"])
    build_digest(D, [item("a", text="hi ![x](http://i/x.png) there")], interests, "", llm, "c", "w", "p", 1, 0, [])
    assert all("![" not in c[3] for c in llm.calls)


def test_ranking_failure_keeps_input_order(interests):
    llm = FakeLLM(json_errors=1, text_replies=["body"])
    md = build_digest(D, [item("a"), item("b")], interests, "", llm, "c", "w", "", 1, 1, [])
    assert "body" in md and "- [b](" in md


def test_budget_exhausted_falls_back_to_plain_list(interests):
    llm = FakeLLM(json_replies=[{"1": 9, "2": 5}], raise_after=1)
    md = build_digest(D, [item("a"), item("b")], interests, "", llm, "c", "w", "", 2, 0, [])
    assert "- [a](https://ex.com/a) — Ex Blog" in md and "- [b](https://ex.com/b) — Ex Blog" in md


def test_ranking_budget_and_summary_llmerror(interests):
    llm = FakeLLM(raise_after=0)
    md = build_digest(D, [item("a")], interests, "", llm, "c", "w", "", 1, 0, [])
    assert "- [a](https://ex.com/a) — Ex Blog" in md
    md = build_digest(D, [item("a")], interests, "", FakeLLM(json_replies=[{"1": 1}]), "c", "w", "", 1, 0, [])
    assert "- [a](https://ex.com/a) — Ex Blog" in md


def test_auth_error_degrades_with_notice(interests):
    from rss_digest.llm import LLMAuthError

    class AuthFail(FakeLLM):
        def chat_json(self, *a):
            raise LLMAuthError("401")
        chat = chat_json
    md = build_digest(D, [item("a")], interests, "", AuthFail(), "c", "w", "", 1, 0, [])
    assert "- [a](https://ex.com/a) — Ex Blog" in md
    assert "Summaries unavailable today (LLM error, see logs)." in md


def test_summary_fallback_adds_notice(interests):
    md = build_digest(D, [item("a")], interests, "", FakeLLM(json_replies=[{"1": 1}]), "c", "w", "", 1, 0, [])
    assert "Summaries unavailable today (LLM error, see logs)." in md


def test_explore_lists_titles_from_below_the_cut_off(interests):
    llm = FakeLLM(json_replies=[{"1": 9, "2": 8, "3": 1, "4": 0}], text_replies=["## K\n- [a](https://ex.com/a): s"])
    md = build_digest(D, [item(t) for t in "abcd"], interests, "", llm, "c", "w", "", top_n=1, also_notable=1, flagged_pages=[], explore=5)
    explore = md.split("## Outside your usual\n")[1].split("\n\n")[0]
    assert sorted(explore.splitlines()) == ["- [c](https://ex.com/c) — Ex Blog", "- [d](https://ex.com/d) — Ex Blog"]
    assert len(llm.calls) == 2  # titles only: the writer model saw just the top article
