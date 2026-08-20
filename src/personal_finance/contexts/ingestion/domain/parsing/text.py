from __future__ import annotations

import html
from html.parser import HTMLParser
import quopri
import re


_SOFT_BREAK = re.compile(r"=\r?\n")
_QUOTED_PRINTABLE = re.compile(r"=[0-9A-Fa-f]{2}")
_IGNORED_TAGS = frozenset({"script", "style", "head"})


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
