# RSS Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily Python job per instance that writes a short digest of new FreshRSS items and keeps LLM-maintained topic pages, built from starred and Wallabag items, in an Obsidian vault on Nextcloud.

**Architecture:** One package, `rss_digest`, run by a systemd timer: `python -m rss_digest --config <instance>.toml run|backlog`. It has thin HTTP clients (FreshRSS Google Reader API, Wallabag REST, Nextcloud WebDAV, OpenAI-compatible chat) plus pure logic modules (filtering, ranking, page parsing) that are tested with fakes. State lives in SQLite outside the vault.

**Tech Stack:** Python ≥ 3.11 (Yunohost 12 / Debian 12 ships 3.11), `httpx` (all HTTP, tested with `httpx.MockTransport`), `PyYAML` (frontmatter, because Obsidian's properties editor rewrites YAML in block style), `pytest`. Everything else comes from the stdlib (`sqlite3`, `tomllib`, `html.parser`, `logging`).

**Spec:** `docs/superpowers/specs/2026-11-13-rss-digest-design.md`

## Global Constraints

- Runtime dependencies are limited to `httpx` and `PyYAML`. No vendor LLM SDKs: all LLM calls go to `POST {base_url}/chat/completions`.
- Two model slots: `cheap_model` (ranking, assignment) and `writer_model` (summaries, pages).
- The vault is accessed only through WebDAV at `{nextcloud.url}/remote.php/dav/files/{user}/{vault_path}/`.
- Vault layout: `Digest/YYYY-MM-DD.md`, `Topics/<title>.md`, `_config/interests.md`, `_config/page-prompt.md`, `_config/digest-prompt.md`.
- Never overwrite a file Carl wrote. `_config/*` files are seeded only if they're missing. A file in `Topics/` without `type: topic` frontmatter is never touched.
- Topic page sections, in this order: `## Summary`, `## Recent changes`, `## Sources` (AI), then `## My notes` (Carl). Everything from `## My notes` to the end of the file is preserved byte-for-byte.
- Writes are atomic: PUT to a temp file, then MOVE.
- No article images in any output. Strip them from the LLM input and from the LLM output.
- One config file and one SQLite file per instance. The code holds no global or shared state between instances.
- Topic pages are built only from kept items (FreshRSS stars and Wallabag entries), never from digest items.
- Defaults: digest `top_n = 15`, `also_notable = 10`, `max_tokens_per_run = 200000`.

## Review Focus

1. **First run with no `last_digest_run`** must not digest the whole FreshRSS history. Use the last 24 hours. Test in Task 9.
2. **The LLM returns malformed JSON, or ids that weren't in the batch.** Retry once, ignore unknown ids, and treat missing ones as score 0 or unassigned. Never crash or invent items. Tests in Tasks 5, 6 and 8.
3. **A run dies midway** (network error or token budget exceeded). Items whose pages weren't written stay unprocessed, `last_*_run` timestamps don't advance, and the next run resumes. Tests in Tasks 8 and 9.
4. **Topic titles with characters Obsidian or Nextcloud reject** (`/ : * ? " < > | # ^ [ ] \`) or non-ASCII must give a valid filename that is stable for the same title. Test in Task 2.
5. **A day with zero new items** writes a short "nothing new" digest with zero LLM calls. Test in Task 6.

---

### Task 1: Scaffold, config, models, state

**Files:**
- Create: `pyproject.toml`, `rss_digest/__init__.py`, `rss_digest/models.py`, `rss_digest/config.py`, `rss_digest/state.py`, `config.example.toml`, `.gitignore`
- Test: `tests/test_config.py`, `tests/test_state.py`

**Interfaces:**
- Produces:
  - `models.Item` (frozen dataclass): `id: str` (`"freshrss:<id>"` / `"wallabag:<id>"`), `source: str`, `url: str`, `title: str`, `text: str` (plain text, no images), `published: datetime` (tz-aware UTC), `feed: str`, `tags: tuple[str, ...] = ()`
  - `config.load_config(path: Path) -> Config`; `config.ConfigError`
  - `Config` dataclass with fields `freshrss: FreshRSSConf(url, user, api_password)`, `wallabag: WallabagConf(url, client_id, client_secret, username, password)`, `nextcloud: NextcloudConf(url, user, app_password, vault_path)`, `llm: LLMConf(base_url, api_key, cheap_model, writer_model, max_tokens_per_run=200000)`, `digest: DigestConf(top_n=15, also_notable=10)`, `state_path: Path`
  - `state.State(path: Path)` with methods `is_processed(item_id) -> bool`, `mark_processed(item_id)`, `has_key(key) -> bool`, `add_key(key)`, `get(name) -> str | None`, `set(name, value: str)`, `page_hash(page) -> str | None`, `set_page_hash(page, h)`. Each write commits immediately, which is what makes runs resumable.

- [ ] **Step 1: Write the failing tests**

```python
def test_load_config_defaults(tmp_path):  # minimal TOML with only required keys
    c = load_config(p)
    assert c.digest.top_n == 15 and c.digest.also_notable == 10
    assert c.llm.max_tokens_per_run == 200000

def test_missing_key_names_it(tmp_path):  # TOML without [llm].writer_model
    with pytest.raises(ConfigError, match="llm.writer_model"):
        load_config(p)

def test_state_persists_across_instances(tmp_path):
    s = State(tmp_path / "s.db"); s.mark_processed("freshrss:1"); s.add_key("u:https://a.b/c"); s.set("last_digest_run", "2026-11-14T06:00:00+00:00"); s.set_page_hash("Kubernetes", "abc")
    s2 = State(tmp_path / "s.db")
    assert s2.is_processed("freshrss:1") and s2.has_key("u:https://a.b/c")
    assert s2.get("last_digest_run") == "2026-11-14T06:00:00+00:00" and s2.page_hash("Kubernetes") == "abc"
    assert s2.get("nope") is None
```

- [ ] **Step 2:** Create a venv and confirm the tests fail. Run: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest -q`. Expected: FAIL (import errors).
- [ ] **Step 3:** Implement. Set up `pyproject.toml` with `requires-python = ">=3.11"`, deps `httpx`, `PyYAML`, extra `dev = ["pytest"]`, and script `rss-digest = "rss_digest.cli:main"`. `.gitignore` covers `.venv/`, `*.db`, `*.toml` except `config.example.toml` and `pyproject.toml`. `config.example.toml` documents every key and says to `chmod 600` it.
- [ ] **Step 4:** Run `.venv/bin/pytest -q`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: scaffold package with config and SQLite state`.

### Task 2: Text utilities

**Files:**
- Create: `rss_digest/textutil.py`
- Test: `tests/test_textutil.py`

**Interfaces:**
- Produces: `normalize_url(url: str) -> str`, `title_key(title: str) -> str`, `html_to_text(html: str) -> str`, `strip_md_images(md: str) -> str`, `safe_filename(title: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_normalize_url():
    assert normalize_url("http://WWW.Example.com/a/?utm_source=x&b=2&a=1&fbclid=z#frag") == "https://example.com/a?a=1&b=2"
    assert normalize_url("https://example.com/a/") == normalize_url("https://example.com/a")

def test_title_key():
    assert title_key("  Kubernetes 1.32: What's New! ") == title_key("kubernetes 1.32 — whats new")

def test_html_to_text_drops_images_and_scripts():
    t = html_to_text('<p>Hello <b>world</b></p><img src="x.png"><figure><img src="y"><figcaption>cap</figcaption></figure><script>evil()</script><p>Bye</p>')
    assert "Hello world" in t and "Bye" in t
    assert "x.png" not in t and "evil" not in t and "<" not in t

def test_strip_md_images():
    assert strip_md_images("a ![alt](http://x/y.png) b ![[pic.png]] c <img src='z'>") == "a  b  c "

@pytest.mark.parametrize("title", ["CI/CD: pipelines?", 'a*b"c<d>e|f#g^h[i]j\\k', "Sécurité réseau", "...", ""])
def test_safe_filename(title):
    f = safe_filename(title)
    assert f and not set('/\\:*?"<>|#^[]') & set(f) and not f.startswith(".") and len(f) <= 100
    assert f == safe_filename(title)

def test_safe_filename_keeps_unicode():
    assert safe_filename("Sécurité réseau") == "Sécurité réseau"
    assert safe_filename("CI/CD: pipelines?") == "CI-CD - pipelines"
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_textutil.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement with `urllib.parse` and `html.parser.HTMLParser`. Drop the query params `utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `ref`. For `safe_filename`: replace `/` with `-`, `:` with ` -`, drop the other forbidden characters, collapse whitespace, strip leading dots and spaces, truncate to 100 characters, and fall back to `"Untitled"`.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add URL/title normalisation, HTML-to-text and filename helpers`.

### Task 3: FreshRSS and Wallabag clients

**Files:**
- Create: `rss_digest/freshrss.py`, `rss_digest/wallabag.py`
- Test: `tests/test_freshrss.py`, `tests/test_wallabag.py` (both use `httpx.MockTransport`)

**Interfaces:**
- Consumes: `Item`, `FreshRSSConf`, `WallabagConf`, `html_to_text`
- Produces:
  - `FreshRSS(conf, client: httpx.Client | None = None)` with `.reading_list(since: datetime) -> list[Item]` and `.starred() -> list[Item]`
  - `Wallabag(conf, client: httpx.Client | None = None)` with `.entries(since: datetime | None) -> list[Item]`

API facts (both clients log in lazily on their first call):
- FreshRSS: `POST {url}/api/greader.php/accounts/ClientLogin` with form `Email`, `Passwd` returns a `Auth=<token>` line. Send it as the header `Authorization: GoogleLogin auth=<token>`. Fetch streams with `GET {url}/api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/{reading-list|starred}?output=json&n=1000[&ot=<unix>][&c=<continuation>]` and follow `continuation` until it's absent. Item fields: `id`, `title`, `published` (unix), `canonical[0].href` (fall back to `alternate[0].href`), `summary.content`, `origin.title`.
- Wallabag: `POST {url}/oauth/v2/token` with form `grant_type=password, client_id, client_secret, username, password` returns `access_token`. Fetch entries with `GET {url}/api/entries.json?perPage=100&page=N&detail=full[&since=<unix>]` and stop once `page >= pages`. Read items from `_embedded.items[]`: `id`, `url`, `title`, `content`, `created_at` (ISO), `tags[].label`. Use `feed="wallabag"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reading_list_paginates_and_maps(mock):  # page 1 has continuation "c1", page 2 has none
    items = FreshRSS(conf, client=mock).reading_list(since=datetime(2026,11,13,tzinfo=UTC))
    assert [i.id for i in items] == ["freshrss:tag:google.com,2005:reader/item/1", "freshrss:tag:google.com,2005:reader/item/2"]
    assert items[0].url == "https://ex.com/a" and items[0].feed == "Ex Blog" and "<" not in items[0].text
    assert mock.requests[1].url.params["ot"] == str(int(datetime(2026,11,13,tzinfo=UTC).timestamp()))
    assert mock.requests[1].headers["Authorization"] == "GoogleLogin auth=TOKEN"

def test_freshrss_login_failure_raises(mock_401):
    with pytest.raises(httpx.HTTPStatusError): FreshRSS(conf, client=mock_401).starred()

def test_wallabag_entries_pages_and_tags(mock):  # pages=2
    items = Wallabag(conf, client=mock).entries(since=None)
    assert len(items) == 3 and items[0].tags == ("k8s", "security") and items[0].id == "wallabag:42"
    assert "since" not in mock.requests[1].url.params
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_freshrss.py tests/test_wallabag.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement both clients as described above. Use a 30 s timeout and `raise_for_status()` on every response.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add FreshRSS (Google Reader API) and Wallabag clients`.

### Task 4: WebDAV vault client

**Files:**
- Create: `rss_digest/webdav.py`
- Test: `tests/test_webdav.py` (an in-memory fake DAV server built on `MockTransport` that handles GET/PUT/MKCOL/MOVE/PROPFIND)

**Interfaces:**
- Consumes: `NextcloudConf`
- Produces: `WebDAV(conf, client: httpx.Client | None = None)` with `.read(path: str) -> str | None` (returns None on 404), `.write(path: str, text: str) -> None`, `.list(dir: str) -> list[str]` (`.md` filenames directly inside `dir`; returns `[]` on 404). Paths are relative to the vault root, and the client URL-encodes each path segment.

- [ ] **Step 1: Write the failing tests**

```python
def test_write_is_atomic_and_creates_dirs(fake):
    WebDAV(conf, client=fake.client).write("Topics/CI-CD - pipelines.md", "hello é")
    assert fake.files["RSS Vault/Topics/CI-CD - pipelines.md"] == "hello é"
    assert [m for m, _ in fake.log].count("PUT") == 1 and ("MOVE" in [m for m, _ in fake.log])
    assert not any(k.endswith(".tmp") for k in fake.files)

def test_read_missing_returns_none(fake):
    assert WebDAV(conf, client=fake.client).read("_config/interests.md") is None

def test_list_returns_md_names_only(fake):  # Topics/ has a.md, b.md, img.png, sub/
    assert sorted(WebDAV(conf, client=fake.client).list("Topics")) == ["a.md", "b.md"]
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_webdav.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. `write` sends MKCOL for each missing parent (ignoring 405 for "already exists"), PUTs `.<name>.tmp`, then MOVEs with `Destination` and `Overwrite: T`. `list` sends PROPFIND with `Depth: 1` and parses `{DAV:}href` with `xml.etree`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add WebDAV client with atomic writes`.

### Task 5: LLM client

**Files:**
- Create: `rss_digest/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `LLMConf`
- Produces: `LLM(conf, client: httpx.Client | None = None)` with `.chat(model: str, system: str, user: str) -> str`, `.chat_json(model, system, user) -> Any`, `.tokens_used: int`, and the exceptions `LLMError` and `BudgetExceeded`.

- [ ] **Step 1: Write the failing tests**

```python
def test_chat_posts_openai_shape_and_counts_tokens(mock):
    llm = LLM(conf, client=mock); assert llm.chat("m", "sys", "hi") == "ok"
    body = json.loads(mock.requests[0].content)
    assert mock.requests[0].url.path.endswith("/chat/completions") and body["model"] == "m"
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert llm.tokens_used == 30  # usage.total_tokens from response

def test_chat_json_accepts_fenced_json(mock_reply('Sure:\n```json\n{"1": 7}\n```')):
    assert llm.chat_json("m", "s", "u") == {"1": 7}

def test_chat_json_retries_once_then_raises(mock_replies(["not json", "still not"])):
    with pytest.raises(LLMError): llm.chat_json("m", "s", "u")
    assert len(mock.requests) == 2

def test_budget_blocks_next_call(mock):  # max_tokens_per_run=25, first call uses 30
    llm.chat("m", "s", "u")
    with pytest.raises(BudgetExceeded): llm.chat("m", "s", "u")
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_llm.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. Send `Authorization: Bearer {api_key}`, use a 120 s timeout, and set `temperature=0.2`. For JSON extraction: strip a ```` ```json ```` fence if there is one, then `json.loads` the text from the first `{`/`[` to the last `}`/`]`. Check the budget before each call.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add OpenAI-compatible LLM client with token budget`.

### Task 6: Interests and digest

**Files:**
- Create: `rss_digest/interests.py`, `rss_digest/digest.py`, `rss_digest/defaults/digest-prompt.md`, `rss_digest/defaults/interests.md`
- Test: `tests/test_interests.py`, `tests/test_digest.py` (`FakeLLM` in `tests/conftest.py` returns canned replies and records calls)

**Interfaces:**
- Consumes: `Item`, `State`, `LLM`, `normalize_url`, `strip_md_images`
- Produces:
  - `Interests(follow: list[str], mute: list[str], mute_feeds: list[str])` and `parse_interests(md: str) -> Interests`. Lines are `follow: <text>`, `mute: <keyword>` and `mute: feed:<feed name>`, case-insensitive. All other lines are ignored.
  - `filter_items(items, interests, state) -> tuple[list[Item], int]`. It drops muted items (keyword in title or feed, or feed in `mute_feeds`), drops duplicates within the batch by `normalize_url`, and drops items whose `"u:"+normalize_url` key is already in state because they've been kept. It returns the kept items and the muted count.
  - `rank(items, interests, profile: str, llm, model) -> list[tuple[Item, float]]`, sorted by score descending. Items go in batches of 40, numbered 1..n in the prompt, and the expected reply is JSON `{"<n>": score 0-10}`. Unknown numbers are ignored and missing ones count as 0.
  - `build_digest(day: date, items, interests, profile, llm, cheap_model, writer_model, digest_prompt, top_n, also_notable, flagged_pages: list[str]) -> str`, which returns the note's markdown.

Digest layout: `# Digest YYYY-MM-DD`, then the writer model's body (summaries of the top N grouped by topic, each with `[title](url)`, passed through `strip_md_images`), then `## Also notable` with the next `also_notable` items as `- [title](url) — feed`. After that comes `Muted: N articles.` and, if any pages were flagged, `## Pages not updated (you edited them)` with `[[Topic]]` links.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_interests():
    i = parse_interests("# notes\nfollow: Kubernetes security\nmute: sponsored\nMUTE: feed:Vendor Blog\nrandom line")
    assert i.follow == ["Kubernetes security"] and i.mute == ["sponsored"] and i.mute_feeds == ["Vendor Blog"]

def test_filter_mutes_dedups_and_skips_kept(state):
    state.add_key("u:" + normalize_url("https://ex.com/kept"))
    kept, muted = filter_items([item("Sponsored: buy"), item("x", feed="Vendor Blog"), item("a", url="https://ex.com/a?utm_source=1"), item("a2", url="https://ex.com/a"), item("k", url="https://ex.com/kept")], interests, state)
    assert [i.title for i in kept] == ["a"] and muted == 2

def test_rank_ignores_unknown_and_defaults_missing():
    llm = FakeLLM(json_replies=[{"1": 9, "3": 4, "99": 10}])
    ranked = rank([item("a"), item("b"), item("c")], interests, "", llm, "cheap")
    assert [(i.title, s) for i, s in ranked] == [("a", 9), ("c", 4), ("b", 0)]

def test_empty_day_makes_no_llm_calls():
    llm = FakeLLM()
    md = build_digest(date(2026,11,14), [], interests, "", llm, "c", "w", "", 15, 10, [])
    assert "No new articles" in md and llm.calls == []

def test_digest_has_no_images_and_lists_flags():
    llm = FakeLLM(json_replies=[{"1": 9, "2": 5}], text_replies=["## K8s\n![x](http://i/x.png)\n- [a](https://ex.com/a): summary"])
    md = build_digest(date(2026,11,14), [item("a"), item("b")], interests, "", llm, "c", "w", "", top_n=1, also_notable=10, flagged_pages=["Kubernetes"])
    assert "![" not in md and "- [b](" in md and "[[Kubernetes]]" in md
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_interests.py tests/test_digest.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. The rank prompt includes the follow and mute lines plus `profile`, and each item as `n. [feed] title — first 300 chars of text`. The summarise prompt is `digest_prompt` plus the top items, each with its first 1500 characters. The default `digest-prompt.md` asks for: 2–3 lines per item, grouping under `##` topic headings, every item linked, no images, English.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add interests parsing and three-stage digest`.

### Task 7: Topic page model

**Files:**
- Create: `rss_digest/pages.py`, `rss_digest/defaults/page-prompt.md`
- Test: `tests/test_pages.py`

**Interfaces:**
- Produces:
  - `Page` dataclass: `front: dict` (with keys `type: topic`, `title`, `description`, `updated` (ISO date), `sources: list[dict(url, title, id, date)]`), `summary: str`, `recent: str`, `my_notes_tail: str`. The tail is the raw text from the `## My notes` line to EOF.
  - `parse_page(text: str) -> Page`, which raises `PageParseError`
  - `render_page(page: Page) -> str`
  - `new_page(title: str) -> Page`, whose tail is `"## My notes\n"`
  - `ai_hash(text: str) -> str`, the sha256 of everything before the `## My notes` line
  - `is_topic_page(text: str) -> bool`, True only if the frontmatter has `type: topic`

- [ ] **Step 1: Write the failing tests**

```python
def test_roundtrip_preserves_my_notes_bytes():
    tail = "## My notes\n\nFrom training 2026-10:\n![](../_attachments/slide.png)\n\n  trailing spaces  \n"
    p = new_page("Kubernetes"); p.my_notes_tail = tail; p.summary = "S"; p.front["sources"] = [{"url": "https://a", "title": "A", "id": "wallabag:1", "date": "2026-11-01"}]
    out = render_page(p)
    assert out.endswith(tail) and parse_page(out).my_notes_tail == tail
    assert out.index("## Summary") < out.index("## Recent changes") < out.index("## Sources") < out.index("## My notes")
    assert "- [A](https://a) — 2026-11-01" in out

def test_parse_rejects_missing_or_duplicate_my_notes():
    with pytest.raises(PageParseError): parse_page(render_page(new_page("X")).replace("## My notes", "## Notes"))
    with pytest.raises(PageParseError): parse_page(render_page(new_page("X")) + "\n## My notes\n")

def test_parse_accepts_block_style_yaml_from_obsidian():
    text = "---\ntype: topic\ntitle: X\ndescription: d\nupdated: 2026-11-14\nsources:\n  - url: https://a\n    title: A\n    id: freshrss:1\n    date: 2026-11-01\n---\n## Summary\ns\n## Recent changes\n## Sources\n## My notes\n"
    assert parse_page(text).front["sources"][0]["id"] == "freshrss:1"

def test_hand_made_page_is_not_topic():
    assert not is_topic_page("# My own page\n") and not is_topic_page("---\ntags: [x]\n---\nhi")

def test_ai_hash_ignores_my_notes():
    a = render_page(new_page("X")); assert ai_hash(a) == ai_hash(a + "my scribbles\n") != ai_hash(a.replace("## Summary\n", "## Summary\nedited\n"))
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_pages.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement with `yaml.safe_load` and `yaml.safe_dump(sort_keys=False, allow_unicode=True)`. Normalise `date` values to ISO strings, since YAML parses them as `date` objects. The default `page-prompt.md` asks for: a `## Summary` of the current state of knowledge (≤ 250 words), `## Recent changes` with ≤ 5 dated bullets (newest first), reading `## My notes` but never repeating or contradicting it silently, no images, and English. The `description` is one line, ≤ 20 words.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add topic page parse/render with protected My notes`.

### Task 8: Vault update

**Files:**
- Create: `rss_digest/vault.py`
- Test: `tests/test_vault.py` (uses `FakeLLM` and an in-memory `FakeDav` with the `read`, `write` and `list` methods)

**Interfaces:**
- Consumes: `WebDAV` (duck-typed), `State`, `LLM`, `pages.*`, `normalize_url`, `title_key`, `safe_filename`, `strip_md_images`
- Produces:
  - `TopicRef(filename: str, title: str, description: str)` and `list_topics(dav) -> list[TopicRef]`, which includes only `is_topic_page` files
  - `select_new_kept(items, state) -> list[Item]`, which skips items that are already processed and items whose `"u:"+normalize_url` or `"t:"+title_key` key is in state. Duplicates within the list are deduplicated the same way.
  - `update_vault(items, dav, state, llm, cheap_model, writer_model, page_prompt, batch_size=20) -> list[str]`, which returns the titles of flagged pages
  - `profile_text(topics: list[TopicRef]) -> str`, with one line per topic, `title: description`, used by the digest ranking

Algorithm per batch of `batch_size` items:
1. Assign: the cheap model gets the existing topics (title and description) and the items numbered 1..n. It replies `{"<n>": ["Existing title" | "NEW: Title"]}`, with at most 2 topics per item. Unknown numbers are ignored. Items it leaves unassigned stay unprocessed.
2. For each topic, read `Topics/<safe_filename(title)>.md`:
   - If the page exists and isn't a topic page, or `parse_page` fails, or `ai_hash(text) != state.page_hash(title)`, flag it, skip it, and leave those items unprocessed.
   - Otherwise (or for a new page), the writer model gets `page_prompt`, the current summary and recent changes, the My notes tail (read-only), and the new items (title, url, first 3000 characters of text). It replies JSON `{"description", "summary", "recent"}`. Pass `summary` and `recent` through `strip_md_images`, append the items to `sources` (deduplicated by url), set `updated` to today, `dav.write` the page, and `state.set_page_hash(title, ai_hash(rendered))`.
3. Only after all of an item's pages are written: `mark_processed(item.id)`, `add_key("u:..")`, `add_key("t:..")`.
4. `BudgetExceeded` stops the whole update. The current batch's unwritten items stay unprocessed, and the exception is re-raised.

- [ ] **Step 1: Write the failing tests**

```python
def test_select_new_kept_dedups_star_and_wallabag(state):
    a = item("Same", url="https://ex.com/p?utm_source=x", id="freshrss:1"); b = item("Same", url="https://ex.com/p", id="wallabag:9")
    assert [i.id for i in select_new_kept([a, b], state)] == ["freshrss:1"]

def test_new_topic_created_and_items_processed(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: CI/CD"]}, {"description": "d", "summary": "s ![](x.png)", "recent": "- 2026-11-14: x"}])
    assert update_vault([item("a")], dav, state, llm, "c", "w", "") == []
    page = parse_page(dav.files["Topics/CI-CD.md"])
    assert page.front["title"] == "CI/CD" and "![" not in page.summary and state.is_processed(item("a").id)

def test_edited_page_is_flagged_and_untouched(dav, state):
    # existing page written by us, then Carl edits Summary
    dav.files["Topics/K8s.md"] = edited; before = edited
    llm = FakeLLM(json_replies=[{"1": ["K8s"]}])
    assert update_vault([item("a")], dav, state, llm, "c", "w", "") == ["K8s"]
    assert dav.files["Topics/K8s.md"] == before and not state.is_processed(item("a").id)

def test_hand_made_page_never_written(dav, state):
    dav.files["Topics/Mine.md"] = "# my page"; llm = FakeLLM(json_replies=[{"1": ["Mine"]}])
    update_vault([item("a")], dav, state, llm, "c", "w", ""); assert dav.files["Topics/Mine.md"] == "# my page"

def test_my_notes_preserved_on_update(dav, state): ...  # tail bytes identical after rewrite

def test_budget_stop_leaves_unwritten_items_unprocessed(dav, state):
    llm = FakeLLM(json_replies=[{"1": ["NEW: A"], "2": ["NEW: B"]}, {"description": "d", "summary": "s", "recent": ""}], raise_after=2)
    with pytest.raises(BudgetExceeded): update_vault([item("x"), item("y")], dav, state, llm, "c", "w", "")
    assert state.is_processed(item("x").id) and not state.is_processed(item("y").id)

def test_bad_assignment_json_leaves_items_unprocessed(dav, state):
    llm = FakeLLM(json_errors=1)  # chat_json raises LLMError
    assert update_vault([item("a")], dav, state, llm, "c", "w", "") == [] and not state.is_processed(item("a").id)
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_vault.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement as described above. An `LLMError` during assignment logs a warning and skips the batch. An `LLMError` while writing a page skips that page.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: add vault update with edit detection and resumable batches`.

### Task 9: CLI orchestration, deploy files, README

**Files:**
- Create: `rss_digest/cli.py`, `rss_digest/__main__.py`, `deploy/rss-digest@.service`, `deploy/rss-digest@.timer`, `README.md`
- Test: `tests/test_cli.py` (`run_daily` and `run_backlog` take injected clients)

**Interfaces:**
- Consumes: everything above
- Produces: `main(argv: list[str] | None = None) -> int`, `run_daily(conf, fresh, wb, dav, llm, state, now: datetime) -> None`, `run_backlog(conf, fresh, wb, dav, llm, state) -> None`, `seed_config(dav) -> None`

`run_daily` order:
1. `seed_config` writes each `_config/*.md` from `defaults/` only when `dav.read` returns None.
2. Kept items: `fresh.starred()` plus `wb.entries(since=last_kept_run or now-24h)`, passed through `select_new_kept`, then `update_vault`. Set `last_kept_run=now` only if no exception was raised.
3. Digest items: `fresh.reading_list(since=last_digest_run or now-24h)`, then `build_digest` with `profile_text(list_topics(dav))` and the flagged pages. Write `Digest/<now.date()>.md` and set `last_digest_run=now`.
4. If step 2 raises `BudgetExceeded`, still do step 3 and add the line `Vault update paused: token budget reached.` to the digest.

`run_backlog` processes all of `fresh.starred()` and `wb.entries(None)` through `update_vault`. It logs progress, and `BudgetExceeded` ends it with exit code 0 and the message "re-run to continue". `main` parses `--config PATH {run,backlog}`, sets up `logging` at INFO to stderr, and returns 1 on any other exception.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_run_digests_only_last_24h(env):
    run_daily(conf, env.fresh, env.wb, env.dav, env.llm, env.state, now=NOW)
    assert env.fresh.reading_list_since == NOW - timedelta(hours=24)
    assert f"Digest/{NOW.date()}.md" in env.dav.files and env.state.get("last_digest_run") == NOW.isoformat()

def test_seed_never_overwrites(env):
    env.dav.files["_config/interests.md"] = "mine"; seed_config(env.dav)
    assert env.dav.files["_config/interests.md"] == "mine" and "_config/page-prompt.md" in env.dav.files

def test_failure_does_not_advance_timestamps(env):
    env.fresh.reading_list_raises = httpx.ConnectError("down")
    with pytest.raises(httpx.ConnectError): run_daily(conf, env.fresh, env.wb, env.dav, env.llm, env.state, now=NOW)
    assert env.state.get("last_digest_run") is None

def test_budget_hit_still_writes_digest_with_notice(env): ...  # update_vault raises BudgetExceeded → digest contains "Vault update paused"

def test_main_returns_1_on_bad_config(tmp_path):
    assert main(["--config", str(tmp_path / "missing.toml"), "run"]) == 1
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_cli.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement `cli.py`, then the deploy files and README:
   - `rss-digest@.service`: `Type=oneshot`, `User=rss-digest`, `ExecStart=/opt/rss-digest/.venv/bin/rss-digest --config /etc/rss-digest/%i.toml run`.
   - `rss-digest@.timer`: `OnCalendar=*-*-* 06:00`, `Persistent=true`.
   - Each instance (`carl_pro`, `carl`) gets its own config and state file. That's what keeps personal and work data apart.
   - The README covers: install on Yunohost (git clone to `/opt/rss-digest`, venv, `pip install .`); creating the FreshRSS API password, the Wallabag API client and the Nextcloud app password; `systemctl enable --now rss-digest@carl_pro.timer`; running the backlog once by hand; the Obsidian setup (attachments folder `_attachments/`, Wikilinks off, Android sync via Remotely Save or FolderSync); and Q&A with the Copilot for Obsidian plugin set to a custom OpenAI-compatible endpoint, with a note to check that it works on Android before relying on it.
- [ ] **Step 4:** Run `.venv/bin/pytest -q`. Expected: all tests PASS.
- [ ] **Step 5:** Commit: `feat: add CLI, systemd units and README`.
