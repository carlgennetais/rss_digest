import dataclasses
from datetime import UTC, datetime

from conftest import item

from rss_digest.bench import recall_at_k, write_blind


def at(it, day):
    return dataclasses.replace(it, published=datetime(2026, 11, day, 9, tzinfo=UTC))


def test_recall_counts_starred_in_daily_top_k_and_skips_unstarred_days():
    a, b, c, d = (at(item(t), 1) for t in "abcd")
    e = at(item("e"), 2)  # day without stars: never ranked
    seen = []

    def ranker(items):
        seen.append({i.title for i in items})
        return sorted(items, key=lambda i: i.title, reverse=True)  # d, c, b, a

    hits, total = recall_at_k([a, b, c, d, e], {a.id, d.id}, ranker, k=2)
    assert (hits, total) == (1, 2) and seen == [{"a", "b", "c", "d"}]


def test_write_blind_hides_model_names(tmp_path):
    key = write_blind(tmp_path, "CI/CD", {"model-x": "page x", "model-y": "page y"})
    files = sorted(p.name for p in (tmp_path / "CI-CD").iterdir())
    assert files == ["A.md", "B.md"]
    assert {(tmp_path / "CI-CD" / f"{k}.md").read_text() for k in key} == {"page x", "page y"}
    assert sorted(key.values()) == ["model-x", "model-y"]
    assert "model" not in (tmp_path / "CI-CD" / "A.md").read_text()


def test_jev_rank_sends_one_question_per_article_and_sorts_by_probability():
    import json

    import httpx

    from rss_digest.bench import jev_rank
    from rss_digest.interests import Interests

    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append((str(request.url), body))
        answers = {"a1": {"type": "noul", "noul": 0.2}, "a2": {"type": "noul", "noul": 0.9}}  # a3 missing
        return httpx.Response(200, json={"answers": answers, "usage": {"cost": 0.001}})

    a, b, c = item("a"), item("b"), item("c")
    ranked, cost = jev_rank([a, b, c], Interests(follow=["DuckDB"], mute=["sponsored"]), "https://or.ai/api/v1", "k",
                            "~typesafe/jev-latest", client=httpx.Client(transport=httpx.MockTransport(handler)))
    url, body = sent[0]
    assert url == "https://or.ai/api/alpha/decisions" and body["model"] == "~typesafe/jev-latest"
    assert set(body["questions"]) == {"a1", "a2", "a3"} and body["questions"]["a1"]["type"] == "noul"
    assert "DuckDB" in json.dumps(body["state"]) and "a" == body["state"]["articles"]["a1"]["title"]
    assert [(i.title, s) for i, s in ranked] == [("b", 9.0), ("a", 2.0), ("c", 0.0)] and cost == 0.001


def test_auc_counts_ties_as_half():
    from rss_digest.bench import auc

    assert auc([3, 2], [1, 2]) == 0.875 and auc([1], [2]) == 0.0 and auc([5], [5]) == 0.5


def test_mixed_days_keep_every_item_once_and_are_reproducible():
    from rss_digest.bench import mixed_days

    pos, neg = [item(f"p{n}") for n in range(10)], [item(f"n{n}") for n in range(50)]
    days = mixed_days(pos, neg, size=45, seed=1)
    assert [len(d) for d in days] == [45, 15]
    assert sorted(i.title for d in days for i in d) == sorted(i.title for i in pos + neg)
    assert days == mixed_days(pos, neg, size=45, seed=1)
