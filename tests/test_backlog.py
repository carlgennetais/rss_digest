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
