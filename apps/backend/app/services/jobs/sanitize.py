from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

from app.services.jobs.types import MAX_DESCRIPTION_CHARS

ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "ul",
        "ol",
        "li",
        "strong",
        "em",
        "b",
        "i",
        "a",
        "h2",
        "h3",
        "h4",
        "div",
        "span",
        "blockquote",
    }
)
SKIP_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "link", "meta", "noscript"})
VOID_TAGS = frozenset({"br"})
SAFE_HREF = re.compile(r"^(https?:|mailto:)", re.I)
_ESCAPED_HTML = re.compile(r"&lt;(/?)(p|div|ul|li|br|h[1-6]|strong|em|a)\b", re.I)
_BLOCK = frozenset({"p", "div", "li", "h2", "h3", "h4", "blockquote"})


def maybe_unescape_html(raw: str) -> str:
    if not raw:
        return ""
    sample = raw[:400]
    if _ESCAPED_HTML.search(sample) or "&lt;p" in sample.lower():
        return unescape(raw)
    return raw


def sanitize_html(raw: str | None) -> tuple[str, str]:
    """Return (safe_html, plaintext). Scripts and javascript: hrefs are dropped."""
    parser = _Sanitizer()
    parser.feed(maybe_unescape_html(raw or ""))
    parser.close()
    html = parser.html
    text = re.sub(r"[ \t]+\n", "\n", parser.text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(html) > MAX_DESCRIPTION_CHARS:
        html = html[:MAX_DESCRIPTION_CHARS]
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS]
    return html, text


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._html: list[str] = []
        self._text: list[str] = []
        self._skip = 0
        self._open_anchors = 0
        self._skip_anchor = 0

    @property
    def html(self) -> str:
        return "".join(self._html)

    @property
    def text(self) -> str:
        return "".join(self._text)

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip or tag not in ALLOWED_TAGS:
            return
        extra = ""
        if tag == "a":
            href = _safe_href(attrs)
            if href is None:
                self._skip_anchor += 1
                return
            extra = (
                f' href="{escape(href, quote=True)}"'
                ' rel="noopener noreferrer" target="_blank"'
            )
            self._open_anchors += 1
        if tag in VOID_TAGS:
            self._html.append("<br>")
            self._text.append("\n")
            return
        if tag in _BLOCK:
            self._text.append("\n")
        self._html.append(f"<{tag}{extra}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if tag == "a" and getattr(self, "_skip_anchor", 0):
            self._skip_anchor -= 1
            return
        if self._skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag == "a":
            if self._open_anchors <= 0:
                return
            self._open_anchors -= 1
        self._html.append(f"</{tag}>")
        if tag in _BLOCK:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._html.append(escape(data))
        self._text.append(data)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)


def _safe_href(attrs) -> str | None:
    found = None
    for name, value in attrs:
        if name.lower() == "href" and value:
            found = value.strip()
            break
    if not found or not SAFE_HREF.match(found):
        return None
    return found
