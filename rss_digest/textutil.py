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
    CHROME = {"nav", "header", "footer", "aside", "form", "head", "title"}  # dropped in main_only mode
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "pre"}

    def __init__(self, main_only: bool = False):
        super().__init__(convert_charrefs=True)
        self.skip = self.SKIP | (self.CHROME if main_only else set())
        self.parts: list[str] = []  # everything not skipped
        self.regions: dict[str, list[str]] = {"article": [], "main": []}  # first <article>, first <main>
        self.open: dict[str, int] = {"article": 0, "main": 0}
        self.closed: set[str] = set()
        self.skip_depth = 0

    def _emit(self, text):
        self.parts.append(text)
        for r, depth in self.open.items():
            if depth:
                self.regions[r].append(text)

    def handle_starttag(self, tag, attrs):
        if tag in self.skip:
            self.skip_depth += 1
        elif tag in self.open and tag not in self.closed:
            self.open[tag] += 1
        elif tag in self.BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in self.skip:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.open and self.open[tag]:
            self.open[tag] -= 1
            if not self.open[tag]:
                self.closed.add(tag)
        elif tag in self.BLOCK:
            self._emit("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self._emit(data)


def _clean(parts: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(parts).splitlines())
    return "\n".join(line for line in lines if line)


def html_to_text(html: str, main_only: bool = False) -> str:
    """Plain text without images or scripts. main_only: only the first <article>, else <main>, else the
    whole page, always without navigation, header, footer, sidebars and forms."""
    p = _TextExtractor(main_only)
    p.feed(html or "")
    p.close()
    if main_only:
        for region in ("article", "main"):
            if text := _clean(p.regions[region]):
                return text
    return _clean(p.parts)


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
