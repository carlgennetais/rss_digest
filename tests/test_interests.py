from rss_digest.interests import filter_items, parse_interests, rank
from rss_digest.textutil import normalize_url
from conftest import FakeLLM, item


def test_parse_interests():
    i = parse_interests("# notes\nfollow: Kubernetes security\nmute: sponsored\nMUTE: feed:Vendor Blog\nrandom line")
    assert i.follow == ["Kubernetes security"] and i.mute == ["sponsored"] and i.mute_feeds == ["Vendor Blog"]


def test_filter_mutes_dedups_and_skips_kept(state, interests):
    state.add_key("u:" + normalize_url("https://ex.com/kept"))
    kept, muted = filter_items([item("Sponsored: buy"), item("x", feed="Vendor Blog"), item("a", url="https://ex.com/a?utm_source=1"), item("a2", url="https://ex.com/a"), item("k", url="https://ex.com/kept")], interests, state)
    assert [i.title for i in kept] == ["a"] and muted == 2


def test_feed_mute_is_exact_case_insensitive(state, interests):
    kept, muted = filter_items([item("x", feed="vendor blog"), item("y", feed="Vendor Blog Extra")], interests, state)
    assert [i.title for i in kept] == ["y"] and muted == 1


def test_rank_ignores_unknown_and_defaults_missing(interests):
    llm = FakeLLM(json_replies=[{"1": 9, "3": 4, "99": 10}])
    ranked = rank([item("a"), item("b"), item("c")], interests, "", llm, "cheap")
    assert [(i.title, s) for i, s in ranked] == [("a", 9), ("c", 4), ("b", 0)]


def test_rank_scores_coerced_and_stable(interests):
    llm = FakeLLM(json_replies=[{"1": "7", "2": "high", "3": 7.0, "4": None}])
    ranked = rank([item("a"), item("b"), item("c"), item("d")], interests, "", llm, "cheap")
    assert [(i.title, s) for i, s in ranked] == [("a", 7), ("c", 7), ("b", 0), ("d", 0)]


def test_rank_batches_of_40_and_llm_error_scores_zero(interests):
    llm = FakeLLM(json_errors=1, json_replies=[{"1": 5}])
    items = [item(f"t{n}") for n in range(41)]
    ranked = rank(items, interests, "", llm, "cheap")
    assert len(llm.calls) == 2 and [i.title for i, s in ranked if s] == ["t40"]


def test_linkless_items_are_not_deduped(state, interests):
    from dataclasses import replace
    state.add_key("u:https:///")
    a, b = replace(item("a"), url=""), replace(item("b"), url="")
    assert filter_items([a, b], interests, state) == ([a, b], 0)


def test_seeded_interests_file_has_no_active_rules():
    from rss_digest.cli import _default
    i = parse_interests(_default("interests.md"))
    assert i.follow == [] and i.mute == [] and i.mute_feeds == []
