"""A gamebook chapter's hints and its replies to wrong moves.

A study chapter in gamebook mode is played as an exercise: the player finds
the moves of the mainline for their side. Lichess keeps two texts on the move
tree for that, and its PGN export leaves both out (lila's ``PgnDump`` writes
comments, glyphs, drawings and clocks only):

- a hint, on the position where the player is to move, shown on request;
- a reply to wrong moves, on the player's right move, shown when they play any
  other move that has no comment of its own (``gamebookPlayCtrl.ts``:
  ``parNode.children[0].gamebook.deviation``).

The chapter's JSON carries the move tree with both (``treeParts``), so they
are read from there and added to the export as comments (``comments.py``),
labelled the way Lichess's gamebook editor labels them, each on the move it
belongs to: a hint after the comments of the move it hints at the answer to,
a reply after the comments of the right move, before the variations that are
the author's own wrong moves.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

from ...core.fetch import FetchError
from .comments import Note

#: The editor's words for each (``gamebookEdit.ts``).
HINT = "Hint: "
DEVIATION = "When any other wrong move is played: "

_GAMEBOOK = re.compile(r'^\[ChapterMode "gamebook"\][ \t]*$', re.MULTILINE)

#: A node of the tree, with its place in the export and the moves to it.
_Place = tuple[dict[str, Any], tuple[int, ...], tuple[str, ...]]


def is_gamebook(pgn: str) -> bool:
    """Whether a chapter's export says it is a gamebook."""
    return _GAMEBOOK.search(pgn) is not None


def notes(parts: object, subject: str) -> list[Note]:
    """Every hint and reply in a chapter's tree, as its JSON sends it.

    The mainline comes as a flat list (``treeParts``), each entry's
    ``children`` being the variations to the next entry, which comes first;
    a variation's own moves nest as usual. That is the tree lila's client
    rebuilds, and the order the export writes moves in.
    """
    if (
        not isinstance(parts, list)
        or not parts
        or not all(isinstance(p, dict) for p in parts)
    ):
        raise FetchError(f"{subject} carries no moves")
    line = [dict(p) for p in parts]  # copies: the next entry is added to each
    for prev, nxt in pairwise(line):
        sidelines = prev.get("children")
        prev["children"] = [nxt, *(sidelines if isinstance(sidelines, list) else [])]
    found: list[Note] = []
    stack: list[_Place] = [(line[0], (), ())]
    while stack:
        node, path, sans = stack.pop()
        book = node.get("gamebook")
        if isinstance(book, dict):
            for field, label in (("deviation", DEVIATION), ("hint", HINT)):
                text = book.get(field)
                if isinstance(text, str) and text.strip():
                    found.append(Note(path, sans, label + text.strip()))
        children = node.get("children")
        if not isinstance(children, list):
            continue
        # A first move forced to be a variation is exported twice, as the
        # mainline and again as the first variation (lila's
        # ``Branches.variationsOnly``), so each later move is one further on.
        first = children[0] if children else None
        forced = isinstance(first, dict) and first.get("forceVariation") is True
        for i, child in reversed(list(enumerate(children))):
            if isinstance(child, dict):
                san = child.get("san")
                place = i + 1 if forced and i else i
                stack.append((child, (*path, place), (*sans, str(san))))
    return found
