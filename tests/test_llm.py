import json

import httpx
import pytest

from conftest import Recorder
from rss_digest.config import LLMConf
from rss_digest.llm import LLM, BudgetExceeded, LLMError


def conf(max_tokens=200000, base="http://x/v1/"):
    return LLMConf(base, "k", "cheap", "writer", max_tokens)


def make(replies, max_tokens=200000, usage=True):
    replies = list(replies)

    def handler(request):
        r = replies.pop(0)
        if isinstance(r, httpx.Response):
            return r
        body = {"choices": [{"message": {"content": r}}]}
        if usage:
            body["usage"] = {"total_tokens": 30}
        return httpx.Response(200, json=body)

    rec = Recorder(handler)
    return LLM(conf(max_tokens), client=rec.client), rec


def test_chat_posts_openai_shape_and_counts_tokens():
    llm, rec = make(["ok"])
    assert llm.chat("m", "sys", "hi") == "ok"
    req = rec.requests[0]
    body = json.loads(req.content)
    assert str(req.url) == "http://x/v1/chat/completions"
    assert req.headers["Authorization"] == "Bearer k"
    assert body["model"] == "m" and body["temperature"] == 0.2
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert llm.tokens_used == 30


def test_missing_usage_counts_zero():
    llm, _ = make(["ok"], usage=False)
    llm.chat("m", "s", "u")
    assert llm.tokens_used == 0


def test_chat_json_accepts_fenced_json():
    llm, _ = make(['Sure:\n```json\n{"1": 7}\n```'])
    assert llm.chat_json("m", "s", "u") == {"1": 7}


def test_chat_json_accepts_array():
    llm, _ = make(["here: [1, 2] done"])
    assert llm.chat_json("m", "s", "u") == [1, 2]


def test_chat_json_retries_once_then_raises():
    llm, rec = make(["not json", "still not"])
    with pytest.raises(LLMError):
        llm.chat_json("m", "s", "u")
    assert len(rec.requests) == 2


def test_chat_json_retry_succeeds():
    llm, rec = make(["nope", '{"a": 1}'])
    assert llm.chat_json("m", "s", "u") == {"a": 1}
    assert len(rec.requests) == 2


def test_budget_blocks_next_call():
    llm, _ = make(["ok", "ok"], max_tokens=25)
    llm.chat("m", "s", "u")
    with pytest.raises(BudgetExceeded):
        llm.chat("m", "s", "u")
    assert not issubclass(BudgetExceeded, LLMError)


def test_http_error_and_bad_shape_raise_llmerror():
    llm, _ = make([httpx.Response(500, text="boom")])
    with pytest.raises(LLMError):
        llm.chat("m", "s", "u")
    llm, _ = make([httpx.Response(200, json={"choices": []})])
    with pytest.raises(LLMError):
        llm.chat("m", "s", "u")


def test_transport_error_raises_llmerror():
    def boom(request):
        raise httpx.ConnectError("down")

    llm = LLM(conf(), client=Recorder(boom).client)
    with pytest.raises(LLMError):
        llm.chat("m", "s", "u")
