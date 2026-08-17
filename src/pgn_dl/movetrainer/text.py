"""Collapse the MoveTrainer format's HTML comment fragments to plain text."""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    """Collapse HTML comment fragments to plain text."""

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []

    def handle_data(self, data: str) -> None:
        self.out.append(data)

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"br", "p"}:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.out.append("\n")


_SAN_MARKER = re.compile(r"@@SANStart@@(.*?)@@SANEnd@@")
# A "<" that opens nothing a tag could close. HTMLParser reads one as the start
# of a tag and eats the rest of the comment looking for its ">", and chess prose
# compares things ("a<b in the pawn race").
_BARE_LT = re.compile(r"<(?![a-zA-Z/!][^<>]*>)")
# Horizontal whitespace only: the line breaks above are the paragraph structure.
_SPACES = re.compile(r"[^\S\n]+")
_LINE_EDGES = re.compile(r" *\n *")
_BLANK_LINES = re.compile(r"\n{3,}")


def strip_html(s: str) -> str:
    p = _HTMLStripper()
    p.feed(_BARE_LT.sub("&lt;", s))
    # feed() holds back whatever might still be the start of a tag or an entity;
    # close() releases it.
    p.close()
    text = "".join(p.out)
    # SAN markers: keep the move, drop the markers.
    text = _SAN_MARKER.sub(r"\1", text)
    text = _SPACES.sub(" ", text)
    text = _LINE_EDGES.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()
