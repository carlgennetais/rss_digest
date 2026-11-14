from urllib.parse import unquote

import httpx
import pytest

from rss_digest.config import NextcloudConf
from rss_digest.webdav import WebDAV

ROOT = "/remote.php/dav/files/bob/"
conf = NextcloudConf(
    url="https://nc.example", user="bob", app_password="pw", vault_path="RSS Vault"
)


class FakeDAV:
    """In-memory DAV server; files keyed by decoded path under the user's root."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.log: list[tuple[str, str]] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"].startswith("Basic ")
        path = unquote(req.url.path)
        assert path.startswith(ROOT)
        key = path[len(ROOT):].rstrip("/")
        self.log.append((req.method, key))
        if req.method == "MKCOL":
            if key in self.dirs:
                return httpx.Response(405)
            self.dirs.add(key)
            return httpx.Response(201)
        if req.method == "PUT":
            assert key.rsplit("/", 1)[0] in self.dirs
            self.files[key] = req.content.decode()
            return httpx.Response(201)
        if req.method == "MOVE":
            dest = unquote(req.headers["destination"])
            assert dest.startswith("https://nc.example" + ROOT)
            assert req.headers["overwrite"] == "T"
            self.files[dest[len("https://nc.example" + ROOT):]] = self.files.pop(key)
            return httpx.Response(201)
        if req.method == "GET":
            if key not in self.files:
                return httpx.Response(404)
            return httpx.Response(200, content=self.files[key].encode())
        if req.method == "PROPFIND":
            assert req.headers["depth"] == "1"
            if key not in self.dirs:
                return httpx.Response(404)
            from urllib.parse import quote

            hrefs = [ROOT + quote(key) + "/"]
            hrefs += [ROOT + quote(f) for f in self.files if f.rsplit("/", 1)[0] == key]
            hrefs += [ROOT + quote(d) + "/" for d in self.dirs if d.rsplit("/", 1)[0] == key and d != key]
            body = "".join(f"<d:response><d:href>{h}</d:href></d:response>" for h in hrefs)
            return httpx.Response(207, text=f'<d:multistatus xmlns:d="DAV:">{body}</d:multistatus>')
        raise AssertionError(req.method)


@pytest.fixture
def fake():
    return FakeDAV()


def test_write_is_atomic_and_creates_dirs(fake):
    WebDAV(conf, client=fake.client).write("Topics/CI-CD - pipelines.md", "hello é")
    assert fake.files["RSS Vault/Topics/CI-CD - pipelines.md"] == "hello é"
    assert [m for m, _ in fake.log].count("PUT") == 1 and ("MOVE" in [m for m, _ in fake.log])
    assert not any(k.endswith(".tmp") for k in fake.files)


def test_write_overwrites_existing(fake):
    d = WebDAV(conf, client=fake.client)
    d.write("a.md", "one")
    d.write("a.md", "two")
    assert d.read("a.md") == "two"


def test_read_missing_returns_none(fake):
    assert WebDAV(conf, client=fake.client).read("_config/interests.md") is None


def test_read_error_raises():
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        WebDAV(conf, client=c).read("x.md")


def test_list_returns_md_names_only(fake):
    d = WebDAV(conf, client=fake.client)
    for n in ("Topics/a.md", "Topics/b é.md", "Topics/img.png", "Topics/sub/c.md"):
        d.write(n, "x")
    assert sorted(d.list("Topics")) == ["a.md", "b é.md"]


def test_list_missing_dir_is_empty(fake):
    assert WebDAV(conf, client=fake.client).list("Nope") == []
