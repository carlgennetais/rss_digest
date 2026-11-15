import json
import shutil

from conftest import item
from rss_digest.backlog import export, vtt_to_text, yt_transcript


def test_export_writes_items_and_index(tmp_path):
    n = export([item("A", text="x" * 5000), item("B", url="https://youtu.be/abc", text="")], tmp_path,
               transcript=lambda url: "hello transcript")
    assert n == 2
    a, b = (json.loads((tmp_path / f"items/{i}.json").read_text()) for i in (1, 2))
    assert len(a["text"]) == 3000 and not a["thin"] and not a["video"]
    assert b["video"] and b["text"] == "hello transcript" and b["thin"]
    assert a["id"] == item("A").id and a["published"].startswith("2026-11-14")
    lines = (tmp_path / "titles.tsv").read_text().splitlines()
    assert lines[0] == "n\tsource\tdate\tfeed\ttags\tthin\ttitle" and lines[2].startswith("2\tfreshrss\t2026-11-14")


def test_vtt_to_text_strips_timing_and_repeats():
    vtt = ("WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n<c>Hello</c> world\n\n"
           "2\n00:00:02.000 --> 00:00:03.000\nHello world\nnext line\n")
    assert vtt_to_text(vtt) == "Hello world\nnext line"


def test_yt_transcript_without_ytdlp_returns_none(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert yt_transcript("https://youtu.be/abc") is None


def test_run_export_selects_new_kept_items_enriched(tmp_path, state):
    from types import SimpleNamespace

    from rss_digest.cli import run_export

    done = item("done")
    state.mark_processed(done.id)
    fresh = SimpleNamespace(starred=lambda: [done, item("star", text="x" * 2000)])
    wb = SimpleNamespace(entries=lambda since: [item("saved", text="y" * 2000)])
    assert run_export(fresh, wb, state, tmp_path, transcripts=False) == 2
    titles = [json.loads((tmp_path / f"items/{n}.json").read_text())["title"] for n in (1, 2)]
    assert sorted(titles) == ["saved", "star"]


from rss_digest.backlog import import_pages  # noqa: E402
from rss_digest.pages import parse_page  # noqa: E402

PAGE = {"description": "d", "summary": "s", "recent": "- r"}


def setup(folder, items, assignment, out=None, out_raw=None):
    (folder / "assignment.json").unlink(missing_ok=True)  # a fresh export; export refuses once topics exist
    export(items, folder)
    (folder / "assignment.json").write_text(json.dumps(assignment))
    (folder / "out").mkdir(exist_ok=True)
    for title, obj in (out or {}).items():
        (folder / "out" / f"{title}.json").write_text(json.dumps(obj))
    for title, raw in (out_raw or {}).items():
        (folder / "out" / f"{title}.json").write_text(raw)


def test_import_writes_pages_and_marks_items(tmp_path, dav, state):
    setup(tmp_path, [item("a"), item("b")], {"1": ["DuckDB"], "2": ["DuckDB", "dbt"]}, out={"DuckDB": PAGE})
    counts = import_pages(tmp_path, dav, state, "")
    assert counts["written"] == 1 and counts["missing"] == 1
    assert state.is_processed(item("a").id) and not state.is_processed(item("b").id)  # b also needs dbt
    assert len(parse_page(dav.files["Topics/DuckDB.md"]).front["sources"]) == 2
    assert (tmp_path / "todo.txt").read_text().splitlines() == ["dbt\t1\tdbt.json\t2"]


def test_import_ignores_bad_assignment_and_invalid_json(tmp_path, dav, state):
    setup(tmp_path, [item("a")], {"1": ["X"], "99": ["Y"], "1x": "Z", "2": "not a list"}, out_raw={"X": "not json"})
    counts = import_pages(tmp_path, dav, state, "")
    assert counts["invalid"] == 1 and counts["written"] == 0 and dav.files == {}


def test_import_rerun_is_idempotent(tmp_path, dav, state):
    setup(tmp_path, [item("a"), item("b")], {"1": ["DuckDB"], "2": ["DuckDB", "dbt"]}, out={"DuckDB": PAGE, "dbt": PAGE})
    first = import_pages(tmp_path, dav, state, "")
    before = dict(dav.files)
    assert first["written"] == 2 and first["items_processed"] == 2
    assert import_pages(tmp_path, dav, state, "")["written"] == 0 and dav.files == before


def test_import_completes_an_item_across_batches(tmp_path, dav, state):
    setup(tmp_path, [item("a"), item("b")], {"1": ["DuckDB"], "2": ["DuckDB", "dbt"]}, out={"DuckDB": PAGE})
    import_pages(tmp_path, dav, state, "")
    (tmp_path / "out" / "dbt.json").write_text(json.dumps(PAGE))  # the next batch's agent output
    counts = import_pages(tmp_path, dav, state, "")
    assert state.is_processed(item("b").id) and counts["missing"] == 0
    assert len(parse_page(dav.files["Topics/DuckDB.md"]).front["sources"]) == 2  # b not cited twice


def test_import_leaves_edited_page_alone(tmp_path, dav, state):
    setup(tmp_path, [item("a")], {"1": ["DuckDB"]}, out={"DuckDB": PAGE})
    import_pages(tmp_path, dav, state, "")
    edited = dav.files["Topics/DuckDB.md"].replace("## Summary\n", "## Summary\nmine\n")
    dav.files["Topics/DuckDB.md"] = edited
    setup(tmp_path, [item("a"), item("c")], {"1": ["DuckDB"], "2": ["DuckDB"]}, out={"DuckDB": PAGE})
    counts = import_pages(tmp_path, dav, state, "")
    assert counts["flagged"] == 1 and dav.files["Topics/DuckDB.md"] == edited
    assert not state.is_processed(item("c").id)


def test_run_import_prints_counts_and_state_reminder(tmp_path, dav, state, capsys):
    from rss_digest.cli import run_import

    setup(tmp_path, [item("a")], {"1": ["DuckDB"]}, out={"DuckDB": PAGE})
    run_import(dav, state, tmp_path, tmp_path / "state.db")
    printed = capsys.readouterr().out
    assert "written: 1" in printed and f"Copy {tmp_path / 'state.db'} to the server" in printed


def test_export_refuses_once_topics_are_assigned(tmp_path):
    import pytest

    export([item("a")], tmp_path)
    (tmp_path / "assignment.json").write_text("{}")
    with pytest.raises(FileExistsError):
        export([item("b")], tmp_path)
    assert json.loads((tmp_path / "items/1.json").read_text())["title"] == "a"
