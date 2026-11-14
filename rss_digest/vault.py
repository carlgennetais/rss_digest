import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from rss_digest.llm import LLMError
from rss_digest.pages import PageParseError, ai_hash, is_topic_page, new_page, parse_page, render_page
from rss_digest.textutil import normalize_url, safe_filename, strip_md_images, title_key

log = logging.getLogger(__name__)
TOPICS_DIR = "Topics"

ASSIGN_PROMPT = (
    "Assign each numbered article to at most 2 topics. Reply with JSON mapping the article number to a list "
    'of topic titles: an existing title verbatim, or "NEW: Title" for a new topic. Omit articles that fit no topic.'
)


@dataclass
class TopicRef:
    filename: str
    title: str
    description: str


def list_topics(dav) -> list[TopicRef]:
    topics = []
    for name in dav.list(TOPICS_DIR):
        text = dav.read(f"{TOPICS_DIR}/{name}")
        if text is None or not is_topic_page(text):
            continue
        try:
            front = parse_page(text).front
        except PageParseError:
            continue
        topics.append(TopicRef(name, str(front.get("title") or name.removesuffix(".md")),
                               str(front.get("description") or "")))
    return topics


def profile_text(topics: list[TopicRef]) -> str:
    return "\n".join(f"{t.title}: {t.description}" for t in topics)


def select_new_kept(items, state) -> list:
    seen, out = set(), []
    for it in items:
        keys = ("u:" + normalize_url(it.url), "t:" + title_key(it.title))
        if state.is_processed(it.id) or any(state.has_key(k) or k in seen for k in keys):
            continue
        seen.update(keys)
        out.append(it)
    return out


def _titles(value, existing: dict[str, str]) -> list[str]:
    vals = value if isinstance(value, list) else [value]
    out = []
    for v in vals[:2]:
        if not isinstance(v, str):
            continue
        t = v.strip()
        if t[:4].lower() == "new:":
            t = t[4:].strip()
        if t:
            out.append(existing.get(t.casefold(), t))
    return out


def _assign(batch, topics, llm, cheap_model) -> dict[str, list]:
    """Returns {item id: [topic titles]}."""
    existing = {t.title.casefold(): t.title for t in topics}
    listing = "\n".join(f"- {t.title}: {t.description}" for t in topics) or "(none)"
    numbered = "\n".join(f"{n}. {it.title} ({it.feed})" for n, it in enumerate(batch, 1))
    reply = llm.chat_json(cheap_model, ASSIGN_PROMPT, f"Existing topics:\n{listing}\n\nArticles:\n{numbered}")
    if not isinstance(reply, dict):
        raise LLMError("assignment reply is not an object")
    wanted: dict[str, list[str]] = {}  # item id -> topic titles
    for key, value in reply.items():
        try:
            n = int(key)
        except ValueError:
            continue
        if not 1 <= n <= len(batch):
            continue
        it = batch[n - 1]
        wanted[it.id] = _titles(value, existing)
    return wanted


def _write_page(title, text, items, dav, state, llm, writer_model, page_prompt) -> None:
    path = f"{TOPICS_DIR}/{safe_filename(title)}.md"
    page = new_page(title) if text is None else parse_page(text)
    cited = {normalize_url(s["url"]) for s in page.front["sources"] if s.get("url")}
    items = [i for i in items if normalize_url(i.url) not in cited]
    if not items:  # already on the page: no writer call, no rewrite
        return
    user = (f"Topic: {title}\n\n## Current summary\n{page.summary}\n\n## Current recent changes\n{page.recent}\n\n"
            f"## My notes (read-only context, never rewrite)\n{page.my_notes_tail}\n\n## New articles\n"
            + "\n\n".join(f"Title: {i.title}\nURL: {i.url}\n{strip_md_images(i.text)[:3000]}" for i in items))
    reply = llm.chat_json(writer_model, page_prompt, user)
    if not (isinstance(reply, dict) and isinstance(reply.get("summary"), str)
            and isinstance(reply.get("recent"), str)):
        raise LLMError("writer reply needs string summary and recent")
    if isinstance(reply.get("description"), str):
        page.front["description"] = reply["description"]
    page.summary = strip_md_images(reply["summary"])
    page.recent = strip_md_images(reply["recent"])
    have = {s.get("url") for s in page.front["sources"]}
    for i in items:
        if i.url not in have:
            have.add(i.url)
            page.front["sources"].append({"url": i.url, "title": " ".join(i.title.split()), "id": i.id,
                                          "date": i.published.date().isoformat()})
    page.front["updated"] = datetime.now(timezone.utc).date().isoformat()
    out = render_page(page)
    dav.write(path, out)
    state.set_page_hash(title, ai_hash(out))


def update_vault(items, dav, state, llm, cheap_model, writer_model, page_prompt, batch_size=20) -> list[str]:
    flagged: list[str] = []
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        try:
            wanted = _assign(batch, list_topics(dav), llm, cheap_model)
        except LLMError as e:
            log.warning("assignment failed, skipping batch: %s", e)
            continue
        by_id = {it.id: it for it in batch}
        pages: dict[str, tuple[str, list]] = {}  # filename stem -> (title, items)
        needs: dict[str, set] = {}  # item id -> filename stems
        for iid, titles in wanted.items():
            for t in titles:
                stem = safe_filename(t)
                pages.setdefault(stem, (t, []))[1].append(by_id[iid])
                needs.setdefault(iid, set()).add(stem)
        done: set[str] = set()
        for stem, (title, page_items) in pages.items():
            path = f"{TOPICS_DIR}/{stem}.md"
            text = dav.read(path)
            if text is not None:
                try:
                    bad = not is_topic_page(text) or ai_hash(text) != state.page_hash(title)
                    if not bad:
                        parse_page(text)
                except PageParseError:
                    bad = True
                if bad:
                    log.warning("page %s was edited or is not ours, skipping", path)
                    if title not in flagged:
                        flagged.append(title)
                    continue
            try:
                _write_page(title, text, page_items, dav, state, llm, writer_model, page_prompt)
            except LLMError as e:
                log.warning("writing page %s failed, skipping: %s", title, e)
                continue
            done.add(stem)
            for it in page_items:
                if needs[it.id] <= done:
                    state.mark_processed(it.id)
                    state.add_key("u:" + normalize_url(it.url))
                    state.add_key("t:" + title_key(it.title))
    return flagged
