import logging
from datetime import date

from rss_digest.interests import rank
from rss_digest.llm import BudgetExceeded, LLMAuthError, LLMError
from rss_digest.textutil import strip_md_images

log = logging.getLogger(__name__)


def _line(it) -> str:
    return f"- [{it.title}]({it.url}) — {it.feed}"


def build_digest(day: date, items, interests, profile, llm, cheap_model, writer_model,
                 digest_prompt, top_n, also_notable, flagged_pages, *, muted: int = 0) -> str:
    parts = [f"# Digest {day.isoformat()}"]
    if not items:
        parts.append("No new articles today.")
    else:
        try:
            ranked = rank(items, interests, profile, llm, cheap_model)
        except (LLMError, LLMAuthError, BudgetExceeded) as e:
            log.warning("ranking failed, keeping input order: %s", e)
            ranked = [(it, 0.0) for it in items]
        top = [it for it, _ in ranked[:top_n]]
        rest = [it for it, _ in ranked[top_n : top_n + also_notable]]
        user = "\n\n".join(
            f"[{it.feed}] [{it.title}]({it.url})\n{strip_md_images(it.text)[:1500]}" for it in top
        )
        try:
            body = strip_md_images(llm.chat(writer_model, digest_prompt, user)).strip()
        except (LLMError, LLMAuthError, BudgetExceeded) as e:
            log.warning("summary failed, using plain list: %s", e)
            body = "\n".join(_line(it) for it in top) + "\n\nSummaries unavailable today (LLM error, see logs)."
        parts.append(body)
        if rest:
            parts.append("## Also notable\n" + "\n".join(_line(it) for it in rest))
    parts.append(f"Muted: {muted} articles.")
    if flagged_pages:
        parts.append("## Pages not updated (you edited them)\n" + "\n".join(f"- [[{p}]]" for p in flagged_pages))
    return "\n\n".join(parts) + "\n"
