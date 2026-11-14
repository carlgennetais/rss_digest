import posixpath
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlsplit

import httpx

from rss_digest.config import NextcloudConf


class WebDAV:
    """Vault access over WebDAV; paths are relative to the vault root."""

    def __init__(self, conf: NextcloudConf, client: httpx.Client | None = None):
        self.conf = conf
        self.root = f"{conf.url.rstrip('/')}/remote.php/dav/files/{quote(conf.user)}"
        self.client = client or httpx.Client(timeout=30)
        self.auth = (conf.user, conf.app_password)

    def _url(self, path: str) -> str:
        full = posixpath.join(self.conf.vault_path.strip("/"), path.strip("/")).strip("/")
        return f"{self.root}/{quote(full)}"

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, self._url(path), auth=self.auth, **kw)

    def read(self, path: str) -> str | None:
        r = self._req("GET", path)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    def write(self, path: str, text: str) -> None:
        parts = path.strip("/").split("/")
        # MKCOL the vault root and every parent; 405 means it already exists.
        for prefix in [""] + ["/".join(parts[:i]) for i in range(1, len(parts))]:
            r = self._req("MKCOL", prefix)
            if r.status_code != 405:
                r.raise_for_status()
        tmp = "/".join(parts[:-1] + [f".{parts[-1]}.tmp"])
        self._req("PUT", tmp, content=text.encode()).raise_for_status()
        self._req(
            "MOVE", tmp, headers={"Destination": self._url(path), "Overwrite": "T"}
        ).raise_for_status()

    def list(self, dir: str) -> list[str]:
        r = self._req("PROPFIND", dir, headers={"Depth": "1"})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        names = []
        for h in ET.fromstring(r.content).iter("{DAV:}href"):
            href = unquote(urlsplit(h.text or "").path)
            if not href.endswith("/") and href.endswith(".md"):
                names.append(posixpath.basename(href))
        return names
