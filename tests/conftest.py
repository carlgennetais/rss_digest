import httpx


class Recorder:
    """httpx.Client whose requests are answered by `handler` and recorded."""

    def __init__(self, handler):
        self.requests = []

        def wrapped(request):
            self.requests.append(request)
            return handler(request)

        self.client = httpx.Client(transport=httpx.MockTransport(wrapped))


from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402

from rss_digest.interests import Interests  # noqa: E402
from rss_digest.llm import BudgetExceeded, LLMError  # noqa: E402
from rss_digest.models import Item  # noqa: E402
from rss_digest.state import State  # noqa: E402


class FakeLLM:
    """Canned LLM: records calls as (method, model, system, user)."""

    def __init__(self, json_replies=None, text_replies=None, json_errors=0, raise_after=None, tokens_per_call=0):
        self.json_replies = list(json_replies or [])
        self.text_replies = list(text_replies or [])
        self.json_errors = json_errors
        self.raise_after = raise_after
        self.calls = []
        self.tokens_used = 0
        self.tokens_per_call = tokens_per_call
        self.limit = 200000
        self.api_key = "k"
        self.keys = []

    def _record(self, method, model, system, user):
        if self.raise_after is not None and len(self.calls) >= self.raise_after:
            raise BudgetExceeded("fake budget")
        if self.tokens_used >= self.limit:
            raise BudgetExceeded("fake limit")
        self.tokens_used += self.tokens_per_call
        self.calls.append((method, model, system, user))
        self.keys.append(self.api_key)

    def chat_json(self, model, system, user):
        self._record("chat_json", model, system, user)
        if self.json_errors > 0:
            self.json_errors -= 1
            raise LLMError("fake json error")
        if not self.json_replies:
            raise LLMError("no json reply left")
        return self.json_replies.pop(0)

    def chat(self, model, system, user):
        self._record("chat", model, system, user)
        if not self.text_replies:
            raise LLMError("no text reply left")
        return self.text_replies.pop(0)


def item(title, url=None, feed="Ex Blog", id=None, text=""):
    slug = title.replace(" ", "-")
    return Item(
        id=id or f"freshrss:{slug}", source="freshrss", url=url or f"https://ex.com/{slug}",
        title=title, text=text, published=datetime(2026, 11, 14, tzinfo=timezone.utc), feed=feed,
    )


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "state.db")


@pytest.fixture
def interests():
    return Interests(follow=["Kubernetes security"], mute=["sponsored"], mute_feeds=["Vendor Blog"])


class FakeDav:
    """In-memory WebDAV: `files` maps vault-relative paths to text."""

    def __init__(self):
        self.files = {}

    def read(self, path):
        return self.files.get(path)

    def write(self, path, text):
        self.files[path] = text

    def list(self, directory):
        pre = directory.strip("/") + "/"
        return sorted(p[len(pre):] for p in self.files
                      if p.startswith(pre) and "/" not in p[len(pre):] and p.endswith(".md"))


@pytest.fixture
def dav():
    return FakeDav()


@pytest.fixture(autouse=True)
def no_page_fetch(monkeypatch):
    """Tests never touch the network: thin items stay thin unless a test stubs page_text itself."""
    monkeypatch.setattr("rss_digest.fetch.page_text", lambda url, client=None: None)
    monkeypatch.setattr("rss_digest.fetch.yt_transcript", lambda url: None)
