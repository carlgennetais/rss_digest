from rss_digest.state import State


def test_state_persists_across_instances(tmp_path):
    s = State(tmp_path / "s.db")
    s.mark_processed("freshrss:1")
    s.add_key("u:https://a.b/c")
    s.set("last_digest_run", "2026-11-14T06:00:00+00:00")
    s.set_page_hash("Kubernetes", "abc")
    s2 = State(tmp_path / "s.db")
    assert s2.is_processed("freshrss:1") and s2.has_key("u:https://a.b/c")
    assert s2.get("last_digest_run") == "2026-11-14T06:00:00+00:00" and s2.page_hash("Kubernetes") == "abc"
    assert s2.get("nope") is None and not s2.is_processed("freshrss:2") and s2.page_hash("X") is None


def test_set_overwrites(tmp_path):
    s = State(tmp_path / "s.db")
    s.set("k", "1"); s.set("k", "2"); s.set_page_hash("P", "a"); s.set_page_hash("P", "b"); s.mark_processed("x"); s.mark_processed("x")
    assert s.get("k") == "2" and s.page_hash("P") == "b"
