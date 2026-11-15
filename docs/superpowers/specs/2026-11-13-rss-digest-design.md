# RSS digest & self-organising vault — design

Date: 2026-11-13. Status: draft for approval.

## Intent

Carl gets hundreds of new FreshRSS articles a day and has ~1,700 saved items (800 FreshRSS stars + 900 Wallabag, with duplicates) on the work side alone. He can't read them all and can't find things later.

Success, in his words:
1. Once a day, read a **short but useful digest** of his feeds, with links to the originals.
2. **Ask questions** and get answers from articles he saved (e.g. last year).
3. The saved knowledge **self-organises into topic pages with sources**, which he sometimes edits (e.g. adding notes from trainings).

## Goals

- Daily digest note, readable in ~5 minutes, linking to originals.
- Topic pages maintained by the LLM from what Carl **deliberately keeps** (FreshRSS stars, Wallabag entries, his own notes) — never from unread digest items.
- Follow/mute topics (e.g. mute sponsored posts) via a file he edits; interests also inferred from what he keeps.
- One-time import of the existing backlog, de-duplicated, to seed the topic pages.
- Q&A over the vault.
- **Model agnostic**: no lock-in to one LLM vendor.
- Page-writing rules live in an **editable prompt file** in the vault.
- **No article images** in digests or topic pages: text plus links to originals only. Carl's own pasted images (in `## My notes`, stored by Obsidian in `_attachments/`) are untouched.

## Non-goals (v1)

- Replacing FreshRSS, Wallabag, FeedMe or Joplin. They keep working exactly as today; this system only reads from FreshRSS/Wallabag.
- Personal instance. Work (`carl_pro`) first; personal is the same code with a second config later.
- A FreshRSS extension or any UI of our own.
- Automatic merging/splitting/renaming of topics. Carl does that by hand in Obsidian if needed.
- Local LLM hosting.

## Anti-goals (failure even if it "works")

- Overwriting anything Carl wrote.
- Any data crossing between personal and work *sources*: the work instance never reads personal FreshRSS/Wallabag, and vice versa. (Vaults may live in one Nextcloud, see Decided by Carl.)
- A digest that is long enough that he stops reading it.
- Topic pages filled with content he never chose to keep.

## Constraints

- Runs on the Yunohost box (old laptop, 24/7) alongside Nextcloud, FreshRSS, Wallabag. Light on CPU/RAM; no local models.
- Cloud LLM is acceptable (including for work content).
- Separate users per context: `carl` (personal), `carl_pro` (work). Each has its own FreshRSS, Wallabag, Nextcloud.
- Vault = a plain Markdown folder in a Nextcloud (the work vault lives in `carl`'s Nextcloud), opened with Obsidian on desktop (Bluefin Linux) and Android (work profile for work).
- Joplin is end-to-end encrypted, so the server can't read or write it; the vault is separate from Joplin.
- Cost target: a few euros/month per instance.

## Approaches considered

- **A. Scheduled script per user on Yunohost** — pulls via existing APIs, writes notes to Nextcloud; Q&A via an existing Obsidian plugin. Least code to run, predictable, cheap. **Chosen.**
- B. n8n-style visual workflow — digest is easy, but merging into existing pages while preserving user sections is painful and hard to debug.
- C. Autonomous agent (Claude Code-style) maintaining the vault from a rules file — most flexible, but costlier, less predictable, and riskier with write access.
- Chosen: **A, borrowing C's idea**: the page-writing rules live in an editable prompt file in the vault.
- FreshRSS extension was rejected: same LLM cost, can't see Wallabag, ill-suited to digests and Q&A.

## Design

### Components

One Python program, run daily (systemd timer or cron) per instance, configured by an env/config file per instance.

1. **Fetch**
   - FreshRSS via its Google Reader–compatible API: new items since last run, and newly starred items.
   - Wallabag via its REST API (OAuth2): new/updated entries with full content and tags.
2. **Digest** (new FreshRSS items)
   - Stage 1, free: drop muted keywords/feeds, de-duplicate.
   - Stage 2, cheap model: rank remaining items on title + excerpt against `interests.md` and a profile inferred from kept items.
   - Stage 3, better model: summarise the top ~15–20 in 2–3 lines each, grouped by topic.
   - Plus a short "also notable" list of titles and a one-line count of what was muted.
3. **Vault update** (newly kept items: stars + Wallabag)
   - Normalise and de-duplicate by URL (strip tracking params), then by title.
   - LLM assigns each item to existing topic page(s), given the list of page titles + summaries, or proposes a new page.
   - LLM rewrites only the AI-owned sections of affected pages, using `_config/page-prompt.md`.
4. **Backlog import**: the same vault-update path run over all existing stars + Wallabag entries once. Resumable, with a cost cap.
5. **State**: a small SQLite file per instance holding processed item IDs, URL hashes for dedup, and last-run timestamps. It lives outside the vault.

### Vault layout (work instance)

```
Digest/2026-11-13.md        daily digest
Topics/<topic>.md           topic pages
_config/interests.md        follow:/mute: lines, edited by Carl
_config/page-prompt.md      rules for writing topic pages, edited by Carl
(anything else)             Carl's own notes; read for Q&A, never written
```

### Topic page shape

YAML frontmatter loosely following Open Knowledge Format (OKF) conventions: `type`, `updated`, `sources` (URL + Wallabag/FreshRSS ID + date). Strict OKF compliance is not a goal.

```
## Summary         (AI)
## Recent changes  (AI)
## Sources         (AI)
## My notes        (Carl, never touched)
```

Rule: the program rewrites only the AI sections. Everything from `## My notes` onward is preserved byte-for-byte. Pages are written atomically, and the program skips (and logs) a page whose structure it can't parse rather than guessing.

### Model agnostic

- All LLM calls go through the **OpenAI-compatible chat completions API**, with `base_url`, `api_key` and `model` set in config. That covers OpenAI, Anthropic (OpenAI-compatible endpoint), Mistral, OpenRouter, and local Ollama later.
- Two model slots: `cheap_model` (ranking, assignment) and `writer_model` (summaries, pages).
- No vendor-specific SDKs.

### Writing to Nextcloud

Through WebDAV with an app password for the instance's Nextcloud user, so Nextcloud sees changes immediately with no `occ files:scan`. The vault (interests, prompt, existing pages) is also read back over WebDAV.

### Q&A

An existing Obsidian community plugin that chats over the vault with a configurable, OpenAI-compatible provider. It needs to work on Android. No code from us; the setup is documented in the README.

## Decided by Carl

- Daily digest plus Q&A plus self-organising topic pages that he edits.
- Cloud LLM is OK, and the system must be model agnostic.
- Obsidian with a Markdown vault in Nextcloud.
- Topic pages are built only from what he deliberately keeps.
- Approach A with an editable prompt file.
- Work instance first.
- Private GitHub repo `rss_digest`.
- 2026-11-15: the work vault (`LLM wiki`) lives in the personal Nextcloud (`carl`), fed only by `carl_pro`'s FreshRSS and Wallabag.

## My calls (correct if wrong)

- Python; daily schedule; SQLite state outside the vault.
- The digest is a vault note rather than an email.
- Interests and mutes go in `_config/interests.md`.
- OKF-style frontmatter, without strict compliance.
- WebDAV rather than direct disk writes.
- Carl's own notes (e.g. from trainings) go in a page's `## My notes`. The LLM reads that section when rewriting the page. Separate note files are not ingested in v1.
- If Carl edits an AI section, the program detects it by comparing against a stored hash of its last write. It then leaves that page untouched and flags it in the next digest. A hand-made page in `Topics/` without our frontmatter is left alone.
- "Kept" only grows. Archiving in Wallabag is part of Carl's normal workflow, so it doesn't count as removal. Un-starring or deleting doesn't remove anything from the pages in v1.
- Output language is set in the prompt files and defaults to English. A topic gets one page, whatever language its sources are in. The digest's rules go in an editable `_config/digest-prompt.md`, next to `page-prompt.md`.

## Left to the builder

- Exact FreshRSS/Wallabag API calls and paging.
- Prompt wording for ranking, summaries and assignment.
- Which Obsidian Q&A plugin to recommend (must support custom OpenAI-compatible endpoints and Android).
- Default digest size.
- How the digest's run time is scheduled.
