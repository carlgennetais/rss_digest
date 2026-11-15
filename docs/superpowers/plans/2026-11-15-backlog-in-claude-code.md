# Backlog Import in a Claude Code Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the ~1,500-item backlog into the vault using Sonnet subagents in a Claude Code session, instead of paid API calls, without giving up any of the program's vault safeguards.

**Architecture:** Thin or empty items get their text from the article's web page (in code, for the daily run too). The program exports de-duplicated kept items to a local folder. One agent assigns topics, and parallel agents write page JSON into that folder. The program then imports the JSON through the same page-writing code the daily run uses, which handles WebDAV writes, `## My notes` protection, page hashes and processed-item state. Agents never touch the vault.

**Tech Stack:** Python ≥ 3.11, existing `rss_digest` modules, optional external `yt-dlp` for YouTube transcripts, Claude Code (Sonnet) with background subagents.

**Spec:** `docs/superpowers/specs/2026-11-13-rss-digest-design.md` (section "Backlog import": "the same vault-update path run over all existing stars + Wallabag entries once. Resumable, with a cost cap.")

## Global Constraints

- The vault is written only by `backlog-import`, through `write_topic_page`. Agents write only inside the export folder.
- The export folder (default `backlog/`) holds article text: it must be git-ignored, because the repo is public.
- Page fetch for thin items (text < 1,000 characters, not a video): one GET, 15 s timeout, at most 2 MB, HTML only. On any failure the item keeps its stored text.
- No new Python dependency. `yt-dlp` is optional and found on `PATH`. Without it, videos stay title-only.
- Per-item text given to agents: at most 3,000 characters, the same cap the page writer uses.
- Page JSON contract, the same as the API writer's: `{"description": str, "summary": str, "recent": str}`, following `_config/page-prompt.md`.
- At most 2 topics per item, and about 80–150 topics in total.
- Items whose pages are not written stay unprocessed, so they can be re-imported later or picked up by API runs.
- The local state DB written by the import must be copied to the server before the first daily run. Otherwise every page's hash is unknown there.

## Review Focus

1. **A malformed or missing page JSON** (an agent crashed or wrote prose) skips that topic only. Its items stay unprocessed and the other topics still import. Test in Task 5.
2. **The assignment refers to unknown item numbers, or gives an empty or non-list value.** These are ignored, never a crash. Test in Task 5.
3. **Re-running the import after a partial run** writes no duplicate sources and doesn't reprocess done items. Test in Task 5.
4. **A FreshRSS star with an empty feed excerpt and a Wallabag copy of the same URL with full text:** the Wallabag copy wins. Test in Task 1.
5. **A topic page that Carl edited between two import runs** is flagged and left byte-for-byte unchanged. Test in Task 5.

---

## Background: where article text comes from

The program never downloads web pages itself. Each item's text is what the source app stored:

| Source | Text | Measured on Carl's data, 2026-11-15 |
|---|---|---|
| Wallabag entry | The full article, extracted by Wallabag when it was saved | 761 ok, 84 thin (<1,000 characters), 338 empty |
| FreshRSS star | Whatever the feed sent: a full post, an excerpt, or nothing | 247 ok, 91 thin, 470 empty |
| YouTube (both sources) | The video description at most | 449 items, all empty |
| Hacker News Best stars | Only the "Comments" link | 45 of 53 empty |

So a dead link doesn't lose any text: Wallabag and FreshRSS keep their copy. It only leaves a dead source link on the page. An empty item gives the writer nothing but its title. This plan reduces empty items in three ways: by preferring the Wallabag copy of duplicates (Task 1), by reading the article's web page in code for thin non-video items (Task 3, in the daily run too), and with YouTube transcripts (Task 4). After the page fetch, a dead link (an HTTP error, timeout, bot-check page or PDF) leaves the item with its stored text, which is title-only in the worst case.

---

### Task 1: De-duplicate in favour of the copy with the most text

**Files:**
- Modify: `rss_digest/vault.py` (`select_new_kept`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Produces: `select_new_kept(items, state) -> list`. Same signature as today. Among items that share a dedup key, it keeps the one with the longest `text`. This affects the daily run too.

- [ ] **Step 1: Write the failing test**

```python
def test_dedup_keeps_the_copy_with_most_text(state):
    star = item("Same", url="https://ex.com/p", id="freshrss:1", text="")
    saved = item("Same", url="https://ex.com/p?utm_source=x", id="wallabag:9", text="full article " * 100)
    assert [i.id for i in select_new_kept([star, saved], state)] == ["wallabag:9"]
```

- [ ] **Step 2:** Run `uv run pytest tests/test_vault.py -q`. Expected: the new test FAILS and the rest PASS.
- [ ] **Step 3:** Implement by running the existing loop over `sorted(items, key=lambda it: -len(it.text))`. The sort is stable, so items with equal text keep their order.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5:** Commit: `fix: keep the duplicate with the most text`.

### Task 2: Extract the per-page step from `update_vault`

**Files:**
- Modify: `rss_digest/vault.py`
- Test: existing `tests/test_vault.py`, unchanged (it pins the behaviour)

**Interfaces:**
- Produces:
  - `write_topic_page(stem: str, title: str, items: list, dav, state, llm, writer_model: str, page_prompt: str) -> str`. Returns `"written"`, `"flagged"` (edited, unparsable or not our page) or `"failed"` (an LLM or HTTP error). It contains the current loop body: read the page, check it, call `_write_page`, and catch the errors.
  - `mark_kept(items: list, needs: dict[str, set[str]], written: set[str], state) -> None`. Marks an item processed, and adds its `u:`/`t:` keys, only when every stem in `needs[item.id]` is in `written`.
- `update_vault` keeps its signature and behaviour, built on these two functions.

- [ ] **Step 1:** Refactor. No new test: the 20+ existing vault tests are the check.
- [ ] **Step 2:** Run `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 3:** Commit: `refactor: extract write_topic_page and mark_kept`.

### Task 3: Read the web page for thin items

**Files:**
- Create: `rss_digest/fetch.py`
- Modify: `rss_digest/textutil.py` (`html_to_text` gains a main-content mode), `rss_digest/cli.py` (`run_daily` and `run_backlog` enrich kept items before `update_vault`)
- Test: `tests/test_fetch.py`, `tests/test_textutil.py`

**Interfaces:**
- Produces:
  - `html_to_text(html: str, main_only: bool = False) -> str`. With `main_only`, it keeps only the text inside the first `<article>`, or else `<main>`, or else `<body>`, and always drops `nav`, `header`, `footer`, `aside`, `form`, `script`, `style` and `img`. With the default, behaviour is unchanged.
  - `page_text(url: str, client: httpx.Client | None = None) -> str | None`. Makes one GET with `follow_redirects=True`, a 15 s timeout and the User-Agent `rss-digest (+personal reader)`. It returns None on any HTTP error, timeout or non-`text/html` content type, when the body is over 2 MB, or when the result is under 500 characters. That last rule catches bot-check pages such as "Just a moment..." and "Attention Required".
  - `is_video(url: str) -> bool`, which is true when the host contains `youtube.com` or `youtu.be`. Task 4 uses it too.
  - `enrich(items: list[Item], fetch=page_text) -> list[Item]`. For each item with `len(text) < 1000` that isn't a video, it calls `fetch(url)`. A longer result replaces `text` (`dataclasses.replace`). It logs one line with the totals (tried, improved, failed).

- [ ] **Step 1: Write the failing tests**

```python
def test_main_only_prefers_article_and_drops_chrome():
    html = "<body><nav>Menu</nav><header>Site</header><article><h1>T</h1><p>Body text</p><aside>Ad</aside></article><footer>©</footer></body>"
    t = html_to_text(html, main_only=True)
    assert "Body text" in t and not {"Menu", "Site", "Ad", "©"} & set(t.split())

def test_page_text_returns_none_on_dead_or_non_html(mock_transport):
    # 404, timeout, application/pdf, and a 200 bot-check page "<title>Just a moment...</title>" each give None
    for url in ("https://ex.com/404", "https://ex.com/slow", "https://ex.com/f.pdf", "https://ex.com/cf"):
        assert page_text(url, client=mock_transport) is None

def test_enrich_only_thin_non_video_and_keeps_text_on_failure():
    full, thin, video, dead = item("f", text="x" * 2000), item("t", url="https://ex.com/t"), item("v", url="https://youtu.be/a"), item("d", url="https://ex.com/d")
    calls = []
    out = enrich([full, thin, video, dead], fetch=lambda u: calls.append(u) or ("page " * 200 if u.endswith("/t") else None))
    assert calls == ["https://ex.com/t", "https://ex.com/d"]
    assert out[1].text.startswith("page") and out[0] == full and out[2] == video and out[3] == dead
```

- [ ] **Step 2:** Run `uv run pytest tests/test_fetch.py tests/test_textutil.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement. Stream the body with `client.stream` and stop past 2 MB. In `run_daily` and `run_backlog`, call `enrich(...)` on the output of `select_new_kept` before `update_vault`, so only new kept items are fetched. Digest items are not enriched, because ranking and summaries already work from the feed text and fetching 45 pages a day isn't worth it.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all PASS, including the existing CLI tests, which fetch nothing because their items' text is long or the fetch function is stubbed.
- [ ] **Step 5:** Commit: `feat: read the web page for thin kept items`.

### Task 4: `backlog-export`

**Files:**
- Create: `rss_digest/backlog.py`
- Modify: `rss_digest/cli.py` (new commands, plus a `--dir` option and a `--transcripts` flag), `.gitignore` (add `backlog/`)
- Test: `tests/test_backlog.py`

**Interfaces:**
- Consumes: `select_new_kept`, `Item`, `enrich` (Task 3). The CLI enriches the items before calling `export`.
- Produces:
  - `export(items: list[Item], out: Path, transcript=None) -> int`. Writes `out/items/<n>.json` for n = 1..N with the keys `id, source, url, title, feed, tags, published (ISO), text (≤ 3000 chars), thin (len(text) < 1000), video (bool)`. Also writes `out/titles.tsv` with the header `n\tsource\tdate\tfeed\ttags\tthin\ttitle`. Returns N. `transcript(url) -> str | None` is called for video items whose text is thin. A non-None result replaces their text, still capped at 3,000 characters.
  - `vtt_to_text(vtt: str) -> str`. Drops the WEBVTT header, cue numbers, timestamp lines and `<…>` tags, and collapses consecutive duplicate lines. Auto-captions repeat each line.
  - `yt_transcript(url: str) -> str | None`. Returns None if `shutil.which("yt-dlp")` is None or the command fails. Otherwise it runs `yt-dlp --skip-download --write-subs --write-auto-subs --sub-langs "en.*,fr.*" --sub-format vtt -o <tmpdir>/t <url>` with a 60 s timeout and returns `vtt_to_text` of the first `.vtt` file.
  - CLI: `rss-digest --config C backlog-export [--dir backlog] [--transcripts]` runs the same item selection as `run_backlog`. Video means `is_video(url)` from Task 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_export_writes_items_and_index(tmp_path):
    n = export([item("A", text="x" * 5000), item("B", url="https://youtu.be/abc", text="")], tmp_path,
               transcript=lambda url: "hello transcript")
    assert n == 2
    a, b = (json.loads((tmp_path / f"items/{i}.json").read_text()) for i in (1, 2))
    assert len(a["text"]) == 3000 and not a["thin"] and not a["video"]
    assert b["video"] and b["text"] == "hello transcript" and b["thin"]
    assert (tmp_path / "titles.tsv").read_text().splitlines()[0] == "n\tsource\tdate\tfeed\ttags\tthin\ttitle"

def test_vtt_to_text_strips_timing_and_repeats():
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n<c>Hello</c> world\n\n2\n00:00:02.000 --> 00:00:03.000\nHello world\nnext line\n"
    assert vtt_to_text(vtt) == "Hello world\nnext line"

def test_yt_transcript_without_ytdlp_returns_none(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert yt_transcript("https://youtu.be/abc") is None
```

- [ ] **Step 2:** Run `uv run pytest tests/test_backlog.py -q`. Expected: FAIL (import error).
- [ ] **Step 3:** Implement as described above. The CLI passes `transcript=yt_transcript` only with `--transcripts`, and logs how many videos got a transcript.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5:** Commit: `feat: add backlog-export for agent-written pages`.

### Task 5: `backlog-import`

**Files:**
- Modify: `rss_digest/backlog.py`, `rss_digest/cli.py`
- Test: `tests/test_backlog.py` (uses `FakeDav` and `state` from `conftest.py`)

**Interfaces:**
- Consumes: `write_topic_page`, `mark_kept` (Task 2), the export layout (Task 4)
- Produces:
  - `import_pages(folder: Path, dav, state, page_prompt: str) -> dict[str, int]`. Returns the counts `written`, `flagged`, `missing`, `invalid`, `items_processed`. It reads `folder/assignment.json` (`{"<n>": [titles]}`) and rebuilds `Item`s from `items/<n>.json`. It groups them by `safe_filename(title)`, as `update_vault` does. For each stem, it reads `out/<stem>.json`: if the file is missing, that's `missing`; if the JSON is wrong or lacks string `summary`/`recent`, that's `invalid`. Otherwise it calls `write_topic_page(stem, title, items, dav, state, _Reply(obj), "agent", page_prompt)`. Finally it calls `mark_kept`.
  - `_Reply(obj)`: a stand-in for the LLM whose `chat_json(model, system, user)` returns `obj`.
  - CLI: `rss-digest --config C backlog-import [--dir backlog]` prints the counts. When `written > 0`, it also prints the reminder: `Copy <state_path> to the server before enabling the daily timer.`

- [ ] **Step 1: Write the failing tests**

```python
def test_import_writes_pages_and_marks_items(tmp_path, dav, state):
    setup(tmp_path, items=[item("a"), item("b")], assignment={"1": ["DuckDB"], "2": ["DuckDB", "dbt"]},
          out={"DuckDB": {"description": "d", "summary": "s", "recent": "- r"}})  # no dbt output
    counts = import_pages(tmp_path, dav, state, "")
    assert counts["written"] == 1 and counts["missing"] == 1
    assert state.is_processed(item("a").id) and not state.is_processed(item("b").id)  # b also needs dbt
    assert len(parse_page(dav.files["Topics/DuckDB.md"]).front["sources"]) == 2

def test_import_ignores_bad_assignment_and_invalid_json(tmp_path, dav, state):
    setup(tmp_path, items=[item("a")], assignment={"1": ["X"], "99": ["Y"], "1x": "Z"}, out_raw={"X": "not json"})
    assert import_pages(tmp_path, dav, state, "")["invalid"] == 1 and dav.files == {}

def test_import_rerun_is_idempotent(tmp_path, dav, state):
    setup(...)  # as in the first test, with a dbt output too
    import_pages(tmp_path, dav, state, ""); before = dict(dav.files)
    assert import_pages(tmp_path, dav, state, "")["written"] == 0 and dav.files == before

def test_import_leaves_edited_page_alone(tmp_path, dav, state):
    setup(...); import_pages(tmp_path, dav, state, "")
    dav.files["Topics/DuckDB.md"] = edited = dav.files["Topics/DuckDB.md"].replace("## Summary\n", "## Summary\nmine\n")
    add_items_and_rerun(...)  # a new item assigned to DuckDB
    assert dav.files["Topics/DuckDB.md"] == edited
```

`setup` is a test helper that writes `items/`, `assignment.json` and `out/` the way Task 4 and the agents would.

- [ ] **Step 2:** Run `uv run pytest tests/test_backlog.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement as described above. Items already processed in state are dropped before grouping, which is what makes re-runs a no-op.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all PASS.
- [ ] **Step 5:** Commit: `feat: add backlog-import from agent-written page JSON`.

### Task 6: Agent prompts and runbook

**Files:**
- Create: `docs/backlog-in-claude-code.md`

Contents, in order:

1. **Before starting.** Note what this costs: about 4M Sonnet tokens of your Claude plan, which on Pro will spread over several days of limits. Work content goes through your personal account, and scripted bulk use isn't clearly "ordinary" plan use.
2. **Setup.** Copy `config.toml` to `config.backlog.toml` and set `state_path = "backlog/state.db"`. Install `yt-dlp` (`brew install yt-dlp`) if you want transcripts.
3. **Export:** `uv run rss-digest --config config.backlog.toml backlog-export --transcripts`. Expect the YouTube step to take 20–40 minutes for about 450 videos. YouTube may throttle it; re-running is safe.
4. **Assign.** The prompt for one Sonnet agent: read `backlog/titles.tsv`, choose 80–150 topics named the way a person would title a note, and give each item 1–2 topics. Items that clearly belong to no lasting topic go to `"Misc"`. Write `backlog/assignment.json` as `{"<n>": ["Topic", ...]}`, using only `n` values from the file. Also write `backlog/topics.md` listing each topic with its item count, for review.
5. **Review topics.** Read `backlog/topics.md`. Rename or merge topics by editing `assignment.json` before writing anything.
6. **Write pages.** The prompt for each writer agent: you get a list of topics. For each topic, read `_config/page-prompt.md` (from the vault, or a copy in `backlog/`) and every item of that topic. Items marked `thin` already had their web page read during export. If one is still thin, use its title only and don't guess its content. Don't use web tools. Write `backlog/out/<safe filename>.json` with exactly `{"description", "summary", "recent"}`. Never write anywhere else. The main session splits the topics into 5–6 slices and starts one background agent per slice, using the Sonnet model.
7. **Trial first.** Do steps 4–6 for 2 topics only, then run `backlog-import` and read those pages in Obsidian before launching every slice.
8. **Import:** `uv run rss-digest --config config.backlog.toml backlog-import`. It's safe to re-run after a limit pause, since only missing topics get written.
9. **Hand-over to the server.** Copy `backlog/state.db` to `/var/lib/rss-digest/carl_pro.db` (owner `rss-digest`, mode 600) before enabling `rss-digest@carl_pro.timer`. Then delete `backlog/`, which holds article text.

- [ ] **Step 1:** Write the runbook with the two agent prompts verbatim in fenced blocks.
- [ ] **Step 2:** Check that every command in it matches the CLI from Tasks 4–5: `uv run rss-digest --help` lists `backlog-export` and `backlog-import`.
- [ ] **Step 3:** Commit: `docs: runbook for the backlog in a Claude Code session`.
