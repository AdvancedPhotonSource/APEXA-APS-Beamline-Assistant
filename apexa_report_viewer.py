"""Bounded, offline HTML previews for the scientific canvas.

Only static document markup is retained. The browser additionally isolates it
in a script-free sandbox with a restrictive CSP. Relative raster figures must
resolve inside the report directory, including after resolving symlinks.
"""
import base64
import html
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

MAX_REPORT_BYTES = 25_000_000
MAX_FIGURES = 100
_TAGS = set("html head body title style div section article header footer main nav aside h1 h2 h3 h4 h5 h6 p pre code blockquote strong em b i u s sub sup span table thead tbody tfoot tr td th caption colgroup col img figure figcaption details summary hr br ol ul li dl dt dd a".split())
_DROP_CONTENT = {"script", "iframe", "object", "svg", "template"}
_ATTRS = set("id class style title lang dir colspan rowspan scope width height alt open start reversed type align valign".split())


class _StaticReport(HTMLParser):
    def __init__(self, root: Path, size: int):
        super().__init__(convert_charrefs=False)
        self.root, self.size = root, size
        self.output, self.warnings = [], []
        self.title_parts = []
        self.in_title = False
        self.skipped = []
        self.tags = 0
        self.figures = {}

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def figure(self, src):
        if src.startswith("data:image/"):
            return src
        try:
            parsed = urlsplit(src)
        except ValueError:
            self.warn("An invalid figure URL was omitted.")
            return ""
        if parsed.scheme or parsed.netloc or not parsed.path or parsed.query:
            self.warn("External figures were blocked. Use a self-contained report for offline viewing.")
            return ""
        if src in self.figures:
            return self.figures[src]
        if len(self.figures) >= MAX_FIGURES:
            self.warn("Only the first 100 relative figures can be embedded.")
            return ""
        self.figures[src] = ""
        try:
            candidate = (self.root / unquote(parsed.path)).resolve(strict=True)
            if Path(unquote(parsed.path)).is_absolute() or not candidate.is_relative_to(self.root):
                raise ValueError("Figure is outside the report folder")
            raw = _read_bounded(candidate, MAX_REPORT_BYTES - self.size)
            # A filename selects no image capability: check its actual format.
            with Image.open(io.BytesIO(raw)) as image:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}.get(image.format)
                if not mime:
                    raise ValueError("Use PNG, JPEG, GIF or WebP figures")
                image.verify()
            encoded = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            if self.size + len(encoded) > MAX_REPORT_BYTES:
                raise ValueError("Embedded report exceeds 25 MB")
            self.size += len(encoded)
            self.figures[src] = encoded
            return encoded
        except (OSError, ValueError, RuntimeError, SyntaxError, Image.DecompressionBombError):
            self.warn("Some relative figures could not be embedded (missing, invalid, outside the report folder, or over the size limit).")
            return ""

    def handle_starttag(self, tag, attrs):
        if self.skipped:
            if tag in _DROP_CONTENT:
                self.skipped.append(tag)
            return
        if tag in _DROP_CONTENT:
            self.skipped.append(tag)
            self.warn("This is a static preview. Scripts, embedded applications and inline SVG are omitted; use native canvas plots for interaction.")
            return
        if tag not in _TAGS:
            return
        self.tags += 1
        if tag == "title":
            self.in_title = True
        clean = []
        for key, value in attrs:
            value = value or ""
            if key in _ATTRS or re.fullmatch(r"aria-[a-z-]+", key):
                clean.append((key, value))
            elif tag == "img" and key == "src":
                clean.append((key, self.figure(value)))
            elif tag == "a" and key == "href" and value.startswith("#"):
                clean.append((key, value))
        attributes = "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in clean)
        self.output.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if tag == "title":
            self.in_title = False
        if tag in _TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skipped:
            self.output.append(data)
            if self.in_title:
                self.title_parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def _read_bounded(path: Path, limit: int) -> bytes:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Report exceeds the 25 MB embedded-view limit or is not a file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Report exceeds the 25 MB embedded-view limit")
    return data


def report_payload(path: str) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    if source.suffix.lower() not in (".html", ".htm"):
        raise ValueError("Select an HTML report (.html or .htm)")
    raw = _read_bounded(source, MAX_REPORT_BYTES)
    content = raw.decode("utf-8-sig")
    parser = _StaticReport(source.parent, len(raw))
    parser.feed(content)
    parser.close()
    if not parser.tags or not content.strip() or "\x00" in content:
        raise ValueError("The file does not contain a readable HTML document")
    preview = "".join(parser.output)
    if len(preview.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds the 25 MB embedded-view limit")
    return {"path": str(source), "title": html.unescape("".join(parser.title_parts)).strip() or source.name,
            "html": preview, "warnings": parser.warnings}
