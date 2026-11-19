# Backlog import in a Claude Code session

Imports the existing stars and Wallabag entries into the vault's topic pages, using Sonnet agents in a
Claude Code session instead of paid API calls. It runs in batches, so you can stop when your plan's
usage limit is near and continue another day.

Agents never write to the vault. They write JSON files into `backlog/`, and `backlog-import` writes
the pages through the program's normal code: your `## My notes` sections are protected, edited pages
are left alone, and items are marked processed in the state database.

## Before you start

- **Usage:** agents read every article in full (up to 100,000 characters per item) and full video
  transcripts. Measured on 1,914 kept items, that's about 3.5M tokens of article text, plus roughly
  2–5M for about 450 transcripts: **about 6–9M Sonnet tokens of your plan in total**. A topic page
  averages about 50k tokens in and 700 out, so batches of 5–10 topics keep each session manageable.
- **What goes through your account:** your work articles' text and your interests go through your
  personal Claude account. Scripted bulk use isn't clearly "ordinary" plan use.
- **`backlog/` holds article text.** It is git-ignored. Delete it when you're done.

## 1. Setup (once)

```bash
cp config.toml config.backlog.toml          # then set: state_path = "backlog/state.db"
brew install yt-dlp                          # YouTube transcripts; without it videos stay title-only
mkdir -p backlog && cp ~/Nextcloud/"LLM wiki"/_config/page-prompt.md backlog/
```

## 2. Export (once)

```bash
uv run rss-digest --config config.backlog.toml backlog-export
```

This takes a while:

- It reads the web page of every thin item. Expect 10–20 minutes, or up to about 2 hours if many
  pages time out.
- It fetches subtitles for about 450 videos (if `yt-dlp` is installed), another 20–40 minutes.
  YouTube may throttle it.
  You can re-run the export until you assign topics (step 3). After that it refuses to run, because
  item numbers would change and `assignment.json` would point at the wrong articles.

It writes `backlog/items/<n>.json` and `backlog/titles.tsv`.

**Videos YouTube refused (HTTP 429):** list them in `backlog/videos-retry.tsv` (columns n, url, title) and
retry later, even after assigning topics, since item numbers don't change:

```bash
uv run rss-digest --config config.backlog.toml backlog-retry-videos
```

It fills the transcripts into the item files and keeps only the videos still failing in the list. Retry
before the agents write those videos' topics; a page already imported won't pick up a later transcript.

## 3. Assign topics (once, one agent)

In a Claude Code session in this repo, with the session on Sonnet (`/model sonnet`), say:

```
Read backlog/titles.tsv (columns: n, source, date, feed, tags, thin, title) in full and read every title
yourself; do not classify with keyword rules or a script. Group these saved articles into topics, each named
the way a person would title a note in a knowledge base: specific tools, projects, techniques or ideas
(e.g. "DuckDB", "Semantic layers", "RAG"), not broad fields like "Machine learning" or "Python".
Use the tags column as a strong hint. Give every article 1 or 2 topics; an article that fits no lasting
topic goes to "Misc". At most 40 articles and about 120k tokens of article text per topic (one agent reads
a whole topic at once; text size is len(text)/4 in backlog/items/<n>.json): split bigger subjects into
narrower topics, and use "Misc 2" if Misc gets too big. Write backlog/assignment.json as
{"<n>": ["Topic", ...]} using every n from the file, and backlog/topics.md listing each topic with its
article count, biggest first. Do not write anything else.
```

## 4. Review the topics

Read `backlog/topics.md`. Rename or merge topics now by editing `backlog/assignment.json`, because
each topic name becomes a page in your vault.

Then create the first to-do list (this writes nothing to the vault yet):

```bash
uv run rss-digest --config config.backlog.toml backlog-import
```

`backlog/todo.txt` has one line per page still to write, biggest first, with tab-separated columns:
title, item count, output file, item numbers. `backlog/topics.txt` lists every planned topic and its
file name, so pages can link to topics written in later batches.

## 5. Write pages, one batch at a time

**First batch: a trial of 2 topics.** Check those pages in Obsidian before doing more.

For each batch, say this in the session, changing the number of topics and agents:

```
Take the first 8 lines of backlog/todo.txt and give them to 8 background agents on the Sonnet
model, one topic each (a topic can hold up to about 120k tokens of text), giving each agent its lines and the prompt below.

Agent prompt:
You write topic pages for a personal knowledge base. Read backlog/page-prompt.md once; it gives the rules.
For each of your lines from backlog/todo.txt (columns: title, item count, output file, item numbers):
read backlog/items/<n>.json for each item number (fields: title, url, feed, published, text, thin, video).
Each item file is a single long JSON line that the Read tool cuts short, and long Bash output is cut too,
so first convert each item to a wrapped text file and read those files with the Read tool, in parts when long:
`python3 -c "import json,sys,textwrap; d=json.load(open(sys.argv[1])); open(sys.argv[2],'w').write(f\"{d['title']}\n{d['published']} {d['url']}\n\n\"+textwrap.fill(d['text'],200))" backlog/items/<n>.json /tmp/rss-items/<n>.txt`
(create /tmp/rss-items first).
Read every item's text in full, to the end; never skim or read only the beginning. Use the details
(numbers, tools, arguments, conclusions) in the summary.
Write backlog/out/<output file> containing only this JSON:
{"description": "<one line, at most 20 words>", "kind": "entity" or "concept",
 "summary": "<markdown, at most 250 words, up to 500 when the topic has more than 20 items, no ## headings>",
 "recent": "<markdown bullets, at most 5, newest first, each starting with the article's date>"}
kind: "entity" for a tool, project, company or person; "concept" for an idea, practice or field.
Where it helps, link related topics listed in backlog/topics.txt (columns: title, file) exactly as
[Title](<File.md>). Never link a topic that is not in that file.
Use only what the items say. An item marked thin has little text: use its title and don't guess its content.
No images. Don't use web tools. Don't write any other file.
```

After the agents finish, or when you stop because of your usage limit:

```bash
uv run rss-digest --config config.backlog.toml backlog-import
```

- **What it does:** writes the finished pages to the vault over WebDAV, and Nextcloud syncs them to
  `~/Nextcloud/LLM wiki/Topics/`. It then rewrites `todo.txt` with what's left.
- **Safe to re-run:** pages already written are not duplicated.
- **Bad agent output:** a missing or broken JSON file just stays on the to-do list.
- **Edited pages:** if you edited a topic page in Obsidian between batches, that page is reported as
  `flagged` and left as you wrote it.

Repeat step 5 until `todo.txt` is empty.

## 6. Hand over to the server

Before enabling `rss-digest@carl_pro.timer`, copy `backlog/state.db` to `/var/lib/rss-digest/carl_pro.db`
(owner `rss-digest`, mode 600). It holds which items are done and the fingerprint of every page. Without
it, the server treats every topic page as edited by you and never updates them.

Then delete `backlog/` and `config.backlog.toml`, and run `rss-digest --config config.toml lint` once for
a first maintenance report and `Overview.md`.
