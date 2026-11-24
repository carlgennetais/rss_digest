from datetime import UTC, datetime

import httpx

from rss_digest.config import FreshRSSConf
from rss_digest.models import Item
from rss_digest.textutil import html_to_text


class FreshRSS:
    def __init__(self, conf: FreshRSSConf, client: httpx.Client | None = None):
        self.conf = conf
        self.base = conf.url.rstrip("/") + "/api/greader.php"
        self.client = client or httpx.Client(timeout=30)
        self._auth: dict[str, str] | None = None

    def _login(self) -> dict[str, str]:
        if self._auth is None:
            r = self.client.post(
                f"{self.base}/accounts/ClientLogin",
                data={"Email": self.conf.user, "Passwd": self.conf.api_password},
            )
            r.raise_for_status()
            token = next(
                line[5:] for line in r.text.splitlines() if line.startswith("Auth=")
            )
            self._auth = {"Authorization": f"GoogleLogin auth={token}"}
        return self._auth

    def _stream(self, name: str, since: datetime | None = None) -> list[Item]:
        headers = self._login()
        params: dict[str, str | int] = {"output": "json", "n": 1000}
        if since is not None:
            params["ot"] = int(since.timestamp())
        items: list[Item] = []
        while True:
            r = self.client.get(
                f"{self.base}/reader/api/0/stream/contents/user/-/state/com.google/{name}",
                params=params,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            # FreshRSS's ot also returns old entries it re-saw (vote counts, regenerated blogs); keep only new crawls
            items += [_to_item(e) for e in data.get("items", [])
                      if since is None or int(e.get("crawlTimeMsec", 1e15)) >= since.timestamp() * 1000]
            if not data.get("continuation"):
                return items
            params["c"] = data["continuation"]

    def reading_list(self, since: datetime) -> list[Item]:
        return self._stream("reading-list", since)

    def starred(self) -> list[Item]:
        return self._stream("starred")


def _to_item(e: dict) -> Item:
    link = (e.get("canonical") or e.get("alternate") or [{}])[0].get("href", "")
    return Item(
        id=f"freshrss:{e['id']}",
        source="freshrss",
        url=link,
        title=e.get("title", ""),
        text=html_to_text(e.get("summary", {}).get("content", "")),
        published=datetime.fromtimestamp(e["published"], UTC),
        feed=e.get("origin", {}).get("title", ""),
        read=any(c.endswith("/state/com.google/read") for c in e.get("categories", [])),
    )
