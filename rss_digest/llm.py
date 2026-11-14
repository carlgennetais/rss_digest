import json
import re
from typing import Any

import httpx

from rss_digest.config import LLMConf


class LLMError(Exception):
    pass


class BudgetExceeded(Exception):
    """Deliberately not an LLMError: callers skip a batch on LLMError but must stop the run on this."""


class LLM:
    def __init__(self, conf: LLMConf, client: httpx.Client | None = None):
        self.conf = conf
        self.base = conf.base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=120)
        self.tokens_used = 0
        self.limit = conf.max_tokens_per_run

    def chat(self, model: str, system: str, user: str) -> str:
        if self.tokens_used >= self.limit:
            raise BudgetExceeded(f"{self.tokens_used}/{self.limit} tokens used")
        try:
            r = self.client.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.conf.api_key}"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=120,
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"LLM request failed: {e}") from e
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected LLM response: {str(data)[:200]}") from e
        if not isinstance(content, str):
            raise LLMError("LLM response has no text content")
        usage = data.get("usage") if isinstance(data, dict) else None
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        self.tokens_used += total if isinstance(total, int) else 0
        return content

    def chat_json(self, model: str, system: str, user: str) -> Any:
        for attempt in range(2):
            text = self.chat(model, system, user)
            try:
                return _parse_json(text)
            except ValueError:
                if attempt == 1:
                    raise LLMError(f"LLM reply is not JSON: {text[:200]}") from None


def _parse_json(text: str) -> Any:
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    end = max(text.rfind("}"), text.rfind("]"))
    if not starts or end < min(starts):
        raise ValueError("no JSON found")
    return json.loads(text[min(starts) : end + 1])
