from datetime import UTC, datetime

import httpx

from rss_digest.config import WallabagConf
from rss_digest.models import Item
from rss_digest.textutil import html_to_text


class Wallabag:
    def __init__(self, conf: WallabagConf, client: httpx.Client | None = None):
        self.conf = conf
        self.base = conf.url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)
        self._auth: dict[str, str] | None = None

    def _login(self) -> dict[str, str]:
        if self._auth is None:
            r = self.client.post(
                f"{self.base}/oauth/v2/token",
                data={
                    "grant_type": "password",
                    "client_id": self.conf.client_id,
                    "client_secret": self.conf.client_secret,
                    "username": self.conf.username,
                    "password": self.conf.password,
                },
            )
            r.raise_for_status()
            self._auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self._auth

    def entries(self, since: datetime | None) -> list[Item]:
        headers = self._login()
        params: dict[str, str | int] = {"perPage": 100, "detail": "full"}
        if since is not None:
            params["since"] = int(since.timestamp())
        items: list[Item] = []
        page = 1
        while True:
            r = self.client.get(
                f"{self.base}/api/entries.json",
                params={**params, "page": page},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            items += [_to_item(e) for e in data["_embedded"]["items"]]
            if page >= data.get("pages", 1):
                return items
            page += 1


def _to_item(e: dict) -> Item:
    # %z accepts the colon-less "+0100" offset on 3.11
    created = datetime.strptime(e["created_at"], "%Y-%m-%dT%H:%M:%S%z")
    return Item(
        id=f"wallabag:{e['id']}",
        source="wallabag",
        url=e["url"],
        title=e.get("title") or "",
        text=html_to_text(e.get("content") or ""),
        published=created.astimezone(UTC),
        feed="wallabag",
        tags=tuple(t["label"] for t in e.get("tags", [])),
    )
