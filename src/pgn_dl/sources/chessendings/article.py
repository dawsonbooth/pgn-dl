"""chessendings's articles, read as text and diagrams in page order.

The guides (/guides/<slug>) and each lesson's public page (/endgames/<slug>)
are articles the server renders into plain elements: headings, paragraphs,
tables, FAQ entries, static diagrams with a caption, and two client
components carrying data, the ``ReplayDiagram`` (a position and a line to step
through) and an embedded lesson (``DrillPlayer``).

The title and the paragraphs under it are read from the top of the page. The
rest is read from the element holding the page's first figure: on a guide
that is the whole article, and on an endgame page the section the replay is
in. The links to other lessons and guides below the article sit outside it.
A paragraph only narrow screens show ("Swipe sideways to see every column")
and the date stamp under the title are left out.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from ...core.fetch import FetchError
from . import flight


@dataclass(frozen=True)
class Heading:
    text: str


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Replay:
    """A position and a line, stepped through frame by frame.

    Frame 0 is the starting position and frame ``k`` the position after ``k``
    moves; ``annotations``, when given, holds each frame's drawings.
    """

    fen: str
    pov: str
    moves: tuple[str, ...]
    caption: str
    annotations: tuple[Mapping[str, Any] | None, ...]


@dataclass(frozen=True)
class Lesson:
    """A lesson embedded in the article to be played."""

    slug: str
    title: str
    fen: str


Block = Heading | Paragraph | Replay | Lesson


@dataclass(frozen=True)
class Article:
    title: str
    lede: tuple[str, ...]  # the paragraphs under the title
    blocks: tuple[Block, ...]


_NARROW_ONLY = re.compile(r"(?:^|\s)sm:hidden(?:\s|$)")
_DATE_STAMP = re.compile(r"Updated [A-Z][a-z]+ \d{1,2}, \d{4}")


def _children(node: Any) -> list[Any]:
    if flight.is_element(node):
        node = node[3].get("children")
    if node is None:
        return []
    if isinstance(node, list) and not flight.is_element(node):
        return node
    return [node]


def _hidden(element: list) -> bool:
    classes = element[3].get("className")
    return isinstance(classes, str) and _NARROW_ONLY.search(classes) is not None


def text(node: Any) -> str:
    """The text an element shows, its inline markup flattened, spaces collapsed."""

    def parts(n: Any) -> Iterator[str]:
        if isinstance(n, str):
            yield n
        elif isinstance(n, int | float) and not isinstance(n, bool):
            yield str(n)
        elif flight.is_element(n):
            if not _hidden(n):
                for child in _children(n):
                    yield from parts(child)
        elif isinstance(n, list):
            for child in n:
                yield from parts(child)

    return " ".join("".join(parts(node)).split())


def _is_replay(props: Mapping[str, Any]) -> bool:
    return isinstance(props.get("fen"), str) and isinstance(props.get("moves"), list)


def _replay(props: Mapping[str, Any]) -> Replay:
    moves = props["moves"]
    if not all(isinstance(m, str) and m for m in moves):
        raise FetchError("a chessendings diagram has a move that is not text")
    annotations = props.get("annotations")
    caption = props.get("caption")
    pov = props.get("pov")
    return Replay(
        fen=props["fen"],
        pov=pov if isinstance(pov, str) and pov in ("w", "b") else "",
        moves=tuple(moves),
        caption=" ".join(caption.split()) if isinstance(caption, str) else "",
        annotations=tuple(
            a if isinstance(a, Mapping) else None
            for a in (annotations if isinstance(annotations, list) else ())
        ),
    )


def _table(element: list) -> str:
    """A table as lines: its caption, then each row's cells joined by bars."""
    lines = []
    for node in flight.walk(element[3]):
        if flight.is_element(node) and node[1] in ("caption", "tr"):
            if node[1] == "caption":
                lines.append(text(node))
            else:
                cells = [text(c) for c in _children(node) if flight.is_element(c)]
                lines.append(" | ".join(cells))
    return "\n".join(line for line in lines if line)


def _blocks(node: Any) -> Iterator[Block]:
    """The blocks under ``node``, in page order."""
    if isinstance(node, list) and not flight.is_element(node):
        for child in node:
            yield from _blocks(child)
        return
    if not flight.is_element(node) or _hidden(node):
        return
    tag, props = node[1], node[3]
    if _is_replay(props):
        yield _replay(props)
    elif isinstance(props.get("initialFen"), str) and isinstance(
        props.get("slug"), str
    ):
        title = props.get("title")
        yield Lesson(
            props["slug"], title if isinstance(title, str) else "", props["initialFen"]
        )
    elif tag in ("h1", "h2"):
        if found := text(node):
            yield Heading(found)
    elif tag in ("p", "li", "blockquote", "h3", "h4", "figcaption"):
        if found := text(node):
            yield Paragraph(found)
    elif tag == "table":
        if found := _table(node):
            yield Paragraph(found)
    elif tag not in _SKIPPED:
        for child in _children(node):
            yield from _blocks(child)


#: Elements whose content is never article text.
_SKIPPED = ("nav", "svg", "script", "style")


def _below(node: Any) -> list[Any]:
    """What sits under ``node`` in page order: an element's props, a list's items."""
    if flight.is_element(node):
        return list(node[3].values())
    if isinstance(node, dict):
        return list(node.values())
    if isinstance(node, list):
        return node
    return []


def _first_figure(tree: Any) -> tuple[list, list | None] | None:
    """The page's first figure and the element directly holding it."""
    stack: list[tuple[Any, list | None]] = [(tree, None)]
    while stack:
        node, parent = stack.pop()
        if flight.is_element(node) and node[1] == "figure":
            return node, parent
        holder = node if flight.is_element(node) else parent
        stack.extend((child, holder) for child in reversed(_below(node)))
    return None


def _hero(tree: Any, body: list) -> tuple[str, tuple[str, ...]]:
    """The title and the paragraphs under it: the page's h1 before its body.

    The layout carries a not-found page with an h1 of its own ahead of the
    page, so the page's title is the last h1 before the body. Navigation (the
    breadcrumbs) is not part of it.
    """
    title, lede = "", []
    stack: list[Any] = [tree]
    while stack:
        node = stack.pop()
        if node is body:
            break
        if flight.is_element(node):
            tag = node[1]
            if tag in _SKIPPED or _hidden(node):
                continue
            if tag == "h1":
                title, lede = text(node), []
                continue
            if tag == "p":
                found = text(node)
                if title and found and not _DATE_STAMP.fullmatch(found):
                    lede.append(found)
                continue
        stack.extend(reversed(_below(node)))
    return title, tuple(lede)


def read_article(tree: Any, subject: str) -> Article:
    """A page's article, or a refusal for a page without one."""
    found = _first_figure(tree)
    if found is None or found[1] is None:
        raise FetchError(f"{subject} has no article on its page; the site has changed")
    body = found[1]
    title, lede = _hero(tree, body)
    if not title:
        raise FetchError(f"{subject} has no title on its page; the site has changed")
    return Article(title=title, lede=lede, blocks=tuple(_blocks(body)))
