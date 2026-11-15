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
