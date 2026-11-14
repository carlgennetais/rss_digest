from datetime import date

from rss_digest.digest import build_digest
from conftest import FakeLLM, item

D = date(2026, 11, 14)


def test_empty_day_makes_no_llm_calls(interests):
    llm = FakeLLM()
    md = build_digest(D, [], interests, "", llm, "c", "w", "", 15, 10, [], muted=3)
    assert "No new articles" in md and "Muted: 3 articles." in md and llm.calls == []


def test_digest_has_no_images_and_lists_flags(interests):
    llm = FakeLLM(json_replies=[{"1": 9, "2": 5}], text_replies=["## K8s\n![x](http://i/x.png)\n- [a](https://ex.com/a): summary"])
    md = build_digest(D, [item("a"), item("b")], interests, "", llm, "c", "w", "", top_n=1, also_notable=10, flagged_pages=["Kubernetes"], muted=2)
    assert md.startswith("# Digest 2026-11-14") and "![" not in md and "- [b](" in md
    assert "[[Kubernetes]]" in md and "Muted: 2 articles." in md and "## Also notable" in md


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
