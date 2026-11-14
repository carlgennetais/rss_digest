import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref"}


def normalize_url(url: str) -> str:
    """Canonical form for dedup: https, no www., no tracking params, sorted query, no fragment or trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.startswith("utm_") and k not in TRACKING_PARAMS)
    return urlunsplit(("https", host, path, urlencode(query), ""))


def title_key(title: str) -> str:
    words = re.sub(r"[^\w\s]", "", title.casefold()).split()
    return " ".join(words)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "figure", "picture", "svg", "noscript", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html or "")
    p.close()
    lines = (" ".join(line.split()) for line in "".join(p.parts).splitlines())
    return "\n".join(line for line in lines if line)


_MD_IMAGES = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[\[[^\]]*\]\]|<img\b[^>]*>", re.IGNORECASE)


def strip_md_images(md: str) -> str:
    return _MD_IMAGES.sub("", md)


def safe_filename(title: str) -> str:
    """A filename (without .md) that Obsidian and Nextcloud accept; the same title always gives the same name."""
    name = title.replace("/", "-").replace(":", " -")
    name = re.sub(r'[\\*?"<>|#^\[\]]', "", name)
    name = " ".join(name.split()).lstrip(". ")
    name = name[:100].rstrip(". ")
    return name or "Untitled"
