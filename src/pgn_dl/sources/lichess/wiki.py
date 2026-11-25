"""An opening page's description, read from the page as Lichess serves it.

The description is Lichess's opening wiki: markdown its editors keep in lila's
database, with no API. lila renders it (``OpeningWikiApi.markdown``: lists and
headers, no autolinks or tables) and keeps, of the notes it lists per move
(``<li>SAN: text</li>``), only the page's last move's, as a paragraph
(``OpeningWiki.filterMarkupForMove``). What lands in ``.opening__wiki__markup``
is what a visitor reads, and this reads it as plain text: paragraphs, headers
and rules as paragraph breaks, list items as ``- `` lines, or numbered as their
list numbers them.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from ...core.fetch import FetchError

MARKUP = "opening__wiki__markup"
#: "No description of the opening, yet."
PLACEHOLDER = "opening__wiki__markup__placeholder"

#: Tags that start and end a block of text.
_BLOCKS = frozenset(
    {"p", "div", "ul", "ol", "li", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
    | {"blockquote", "pre", "table", "tr"}
)
_SPACE = re.compile(r"\s+")


class _Wiki(HTMLParser):
    """The text of the first ``.opening__wiki__markup``, as blocks.

    Only ``div`` tags are counted to find the block's end: the markdown inside
    renders none of its own, and an unclosed ``<p>`` or ``<li>``, which HTML
    allows, would throw a count of every tag off.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False
        self.placeholder = False
        self.blocks: list[tuple[str, str]] = []  # (list item marker or "", text)
        self._depth = 0  # divs open inside the block, itself included
        self._text: list[str] = []
        self._item = ""
        self._lists: list[int | None] = []  # the next number of each open <ol>

    @property
    def _inside(self) -> bool:
        return self._depth > 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if not self._inside:
            if tag == "div" and MARKUP in classes and not self.found:
                self.found = True
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        if PLACEHOLDER in classes:
            self.placeholder = True
        if tag == "br":
            self._text.append("\n")
        elif tag in _BLOCKS:
            self._flush()
            if tag in ("ol", "ul"):
                start = dict(attrs).get("start") or "1"
                number = int(start) if tag == "ol" and start.isdigit() else None
                self._lists.append(number)
            elif tag == "li":
                number = self._lists[-1] if self._lists else None
                self._item = "- " if number is None else f"{number}. "
                if number is not None:
                    self._lists[-1] = number + 1

    def handle_endtag(self, tag: str) -> None:
        if not self._inside:
            return
        if tag in _BLOCKS:
            self._flush()
        if tag in ("ol", "ul") and self._lists:
            self._lists.pop()
        if tag == "div":
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._inside:
            self._text.append(_SPACE.sub(" ", data))

    def _flush(self) -> None:
        lines = "".join(self._text).split("\n")
        self._text = []
        text = "\n".join(_SPACE.sub(" ", line).strip() for line in lines).strip()
        if text:
            self.blocks.append((self._item, text))
            self._item = ""


def description(html: str, url: str) -> str | None:
    """The description on the opening page ``html`` (from ``url``), or None.

    None when the page has none: the placeholder, or a block every per-move
    note was filtered out of. A page with no wiki block at all is refused,
    since writing its file without a description would leave it skipped by
    every later run.
    """
    wiki = _Wiki()
    wiki.feed(html)
    wiki.close()
    if not wiki.found:
        raise FetchError(f"{url}: found no description block on the page")
    if wiki.placeholder or not wiki.blocks:
        return None
    parts: list[str] = []
    after_item = False
    for item, text in wiki.blocks:
        if parts:
            parts.append("\n" if item and after_item else "\n\n")
        parts.append(item + text)
        after_item = bool(item)
    return "".join(parts)
