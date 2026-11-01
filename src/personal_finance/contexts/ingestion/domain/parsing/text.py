from __future__ import annotations

import html
from html.parser import HTMLParser
import quopri
import re


_SOFT_BREAK = re.compile(r"=\r?\n")
_QUOTED_PRINTABLE = re.compile(r"=[0-9A-Fa-f]{2}")
# Stricter than `_QUOTED_PRINTABLE`: an encoded `=`, or two escapes in a row
# the way an accented letter is encoded (`=C3=A1`) — never one `=` and two hex
# digits, which `clave=AB12` has, nor a line ending in `=`, which a
# `==========` separator has.
_UNMISTAKABLY_QUOTED_PRINTABLE = re.compile(
    r"=3D|=[0-9A-F]{2}=[0-9A-F]{2}",
    re.IGNORECASE,
)
_IGNORED_TAGS = frozenset({"script", "style", "head"})
# What the layout reader skips: the elements whose text is never shown. Not
# `<head>` as a whole — one left unclosed in a body with no `<body>` tag
# would swallow the alert, and its only shown text is the `<title>`.
_UNSHOWN_TAGS = frozenset({"script", "style", "title"})

# A tag name only counts when what follows it ends the name, so the
# `<alertas@banco.com>` of a plain-text forward is not taken for markup.
_MARKUP = re.compile(
    r"<(?:!doctype|/?(?:html|head|body|style|meta|title|div|span|p|br|table|"
    r"tbody|thead|tr|td|th|font|center|img|a|b|i|u|strong|em|ul|ol|li|"
    r"h[1-6]|section|header|footer)(?=[\s/>]))",
    re.IGNORECASE,
)
_BRACKETED_ADDRESS = re.compile(r"<([^<>\s@]+@[^<>\s@]+)>")
_BLOCK_TAGS = frozenset(
    {"br", "p", "div", "table", "tr", "li", "ul", "ol", "section", "header", "footer"}
    | {f"h{level}" for level in range(1, 7)},
)
_CELL_TAGS = frozenset({"td", "th"})


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        del attrs

        if tag in _IGNORED_TAGS:
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._chunks).split())


class _LayoutCollector(HTMLParser):
    """Like `_TextCollector`, but keeps what the layout says: a block starts a
    new line and two cells of one row stay apart, so a label is still next to
    its own value. An image's `alt` is kept too — a logo is sometimes the only
    place an alert names its bank.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skipping = 0
        self._cells_in_row = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            # Whatever was being skipped is over: a `<style>` nobody closed
            # would otherwise swallow the whole alert.
            self._skipping = 0

        if tag in _UNSHOWN_TAGS:
            self._skipping += 1

        if self._skipping:
            return

        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

        if tag == "tr":
            self._cells_in_row = 0
        elif tag in _CELL_TAGS:
            if self._cells_in_row:
                self._chunks.append(" | ")

            self._cells_in_row += 1
        elif tag == "img":
            alt = dict(attrs).get("alt")

            if alt:
                self._chunks.append(f" {alt} ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _UNSHOWN_TAGS and self._skipping:
            self._skipping -= 1
        elif not self._skipping and tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._chunks).splitlines())

        return "\n".join(line for line in lines if line)


def looks_quoted_printable(raw: str) -> bool:
    return bool(_SOFT_BREAK.search(raw) or _QUOTED_PRINTABLE.search(raw))


def decode_quoted_printable(raw: str) -> str:
    """Undo quoted-printable transport encoding.

    This has to happen before tags are stripped: the encoding breaks lines
    mid-word (`Re=\\ncibiste`) and escapes every `=` as `=3D`, so stripping
    first would leave mangled words and broken attributes behind.
    """
    return quopri.decodestring(raw.encode("utf-8", "replace")).decode(
        "utf-8",
        "replace",
    )


def html_to_text(raw: str) -> str:
    collector = _TextCollector()
    collector.feed(raw)
    collector.close()

    return collector.text


def extract_text(raw: str) -> str:
    """Turn a raw email body into the single line of prose a parser reads.

    Handles the three shapes a provider may deliver: plain text, HTML, and
    either of them wrapped in quoted-printable.
    """
    body = decode_quoted_printable(raw) if looks_quoted_printable(raw) else raw

    if "<" in body and ">" in body:
        return html_to_text(body)

    return " ".join(html.unescape(body).split())


def readable_text(raw: str) -> str:
    """The email as a person would read it, for a reader that understands
    prose — the model — rather than a pattern.

    Not `extract_text`, on purpose: that one flattens everything into the
    single line the templates' patterns were written against, and they stay
    on it. Here an HTML body keeps its lines and its table cells apart, and a
    plain-text body keeps every line break, with only its entities decoded —
    the templates read `&eacute;` as `é`, and a merchant spelled two ways is
    two merchants.

    Transport encoding is undone only when it is unmistakable: guessing at it
    would turn `clave=AB12` in a plain-text alert into something else.
    """
    body = (
        decode_quoted_printable(raw)
        if _UNMISTAKABLY_QUOTED_PRINTABLE.search(raw)
        else raw
    )

    if not _MARKUP.search(body):
        return html.unescape(body)

    collector = _LayoutCollector()
    # A forward whose header block is plain text writes the bank's address in
    # angle brackets, and the HTML parser would drop it as an unknown tag.
    collector.feed(_BRACKETED_ADDRESS.sub(r"&lt;\1&gt;", body))
    collector.close()

    return collector.text
