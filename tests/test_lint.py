from datetime import date

from conftest import FakeDav, FakeLLM
from rss_digest.lint import find_issues, run_lint
from rss_digest.llm import LLMError
from rss_digest.pages import new_page, parse_page, render_page

TODAY = date(2026, 11, 16)


def page(title, summary="", description="d", updated="2026-11-01", kind="concept"):
    p = new_page(title)
    p.front.update(description=description, updated=updated, kind=kind)
    p.summary = summary
    return p


def vault():
    dav = FakeDav()
    for p in (page("DuckDB", "Fast. See [dbt](<dbt.md>).", kind="entity"),
              page("dbt", "Pairs with [DuckDB](<DuckDB.md>)."),
              page("Old idea", updated="2026-03-01", description="")):
        dav.files[f"Topics/{p.front['title']}.md"] = render_page(p)
    return dav


def test_find_issues_orphans_stale_and_missing_descriptions():
    pages = {f.removeprefix("Topics/"): parse_page(t) for f, t in vault().files.items()}
    issues = find_issues(pages, TODAY)
    assert issues["orphans"] == ["Old idea"]
    assert issues["stale"] == ["Old idea (updated 2026-03-01)"]
    assert issues["no_description"] == ["Old idea"]


def test_lint_writes_report_and_overview_and_never_touches_topic_pages():
    dav = vault()
    before = {f: t for f, t in dav.files.items() if f.startswith("Topics/")}
    llm = FakeLLM(json_replies=[{
        "contradictions": ["DuckDB says X, dbt says not X"], "merge_or_split": [], "missing_topics": ["Iceberg"],
        "questions": ["Is DuckLake ready?"],
        "overview": "Your wiki centres on [DuckDB](<Topics/DuckDB.md>) and [Nope](<Topics/Nope.md>)."}])
    report = run_lint(dav, llm, "w", TODAY)
    assert report == "Maintenance/2026-11-16.md"
    text = dav.files[report]
    assert "DuckDB says X" in text and "Iceberg" in text and "Old idea" in text
    overview = dav.files["Overview.md"]
    assert "[DuckDB](<Topics/DuckDB.md>)" in overview and "Topics/Nope.md" not in overview
    assert {f: t for f, t in dav.files.items() if f.startswith("Topics/")} == before
    assert "] lint | " in dav.files["log.md"]


def test_lint_without_llm_still_reports_code_checks():
    dav = vault()

    class Broken(FakeLLM):
        def chat_json(self, model, system, user):
            raise LLMError("down")

    text = dav.files[run_lint(dav, Broken(), "w", TODAY)]
    assert "Old idea" in text and "LLM review failed" in text and "Overview.md" not in dav.files
