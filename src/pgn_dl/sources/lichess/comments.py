"""Comments added to a study chapter's export without writing it again.

Lichess's PGN export of a chapter leaves out some of what the chapter shows:
its description, the study's own description, and a gamebook's hints and
replies to wrong moves (see ``studies.py``). They are added to the exported
text as comments. Nothing already in the text moves or changes, so a file
holds what Lichess sent, plus these.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ...core.fetch import FetchError

#: The tag pairs before the moves, one to a line.
_TAGS = re.compile(r"\A(?:[ \t]*\[[^\n]*\][ \t]*\r?\n)*")
_TOKEN = re.compile(
    r"(?P<comment>\{[^}]*\})"
    r"|;[^\n]*"
    r"|(?P<open>\()"
    r"|(?P<close>\))"
    r"|(?P<nag>\$\d+)"
    r"|\d+\.(?:\.\.)?"
    r"|1-0|0-1|1/2-1/2|\*"
    r"|(?P<san>[^\s{}();$]+)"
)
#: A move's assessment, which Lichess writes onto the move itself ("Nc3!?").
_ASSESSMENT = re.compile(r"[!?]+$")
#: A comment cannot hold a closing brace, so neither may a text put in one.
_BRACES = str.maketrans("{}", "()")


@dataclass(frozen=True)
class Note:
    """A text to add after a move's comments: where in the tree (child
    indices from the root, the mainline first), the moves on the way there,
    to check the place by, and the text itself."""

    path: tuple[int, ...]
    sans: tuple[str, ...]  # one for each step of ``path``
    text: str


@dataclass
class _Move:
    san: str | None
    parent: _Move | None
    #: Where a comment of this move's own goes: after its last annotation.
    #: None for a root with no comment, whose go before the first move.
    end: int | None
    children: list[_Move]


def _tree(pgn: str) -> tuple[_Move, int]:
    """The export's move tree, and where its moves begin."""
    start = _TAGS.match(pgn)
    root = _Move(None, None, None, [])
    first = len(pgn)
    current, outer = root, []
    opening = False  # just inside "(": a comment here precedes its first move
    for m in _TOKEN.finditer(pgn, start.end() if start else 0):
        first = min(first, m.start())
        if (san := m.group("san")) is not None:
            move = _Move(_ASSESSMENT.sub("", san), current, m.end(), [])
            current.children.append(move)
            current, opening = move, False
        elif m.group("open") is not None:
            if current.parent is None:
                raise ValueError("a variation of no move")
            outer.append(current)
            current, opening = current.parent, True
        elif m.group("close") is not None:
            if not outer:
                raise ValueError("a variation closed that was never opened")
            current, opening = outer.pop(), False
        elif (m.group("comment") or m.group("nag")) and not opening:
            current.end = m.end()
    return root, first


def _at(root: _Move, note: Note) -> _Move | None:
    """The move ``note`` belongs after, if every move on its way is there."""
    if len(note.path) != len(note.sans):
        return None
    move = root
    for i, san in zip(note.path, note.sans, strict=True):
        if i >= len(move.children) or move.children[i].san != san:
            return None
        move = move.children[i]
    return move


def _comments(texts: Sequence[str]) -> str:
    return " ".join(f"{{ {text.translate(_BRACES)} }}" for text in texts)


def annotate(
    pgn: str, notes: Sequence[Note], subject: str, *, intro: Sequence[str] = ()
) -> str:
    """``pgn`` with ``intro`` before everything and each note where it belongs.

    ``intro`` comes first, ahead of the export's own first comment. A note
    follows the comments of its move. A note whose place the export does not
    have, or has with another move, is refused rather than put somewhere it
    does not belong.
    """
    if not notes and not intro:
        return pgn
    try:
        root, first = _tree(pgn)
    except ValueError as exc:
        raise FetchError(f"{subject}: its export is not readable PGN ({exc})") from None
    # By offset in ``pgn``; -1 for the root's notes when it has no comment of
    # its own, which go at the start too, after ``intro``.
    added: dict[int, list[str]] = {}
    for note in notes:
        move = _at(root, note)
        if move is None:
            raise FetchError(f"{subject}: its gamebook notes do not fit its moves")
        added.setdefault(-1 if move.end is None else move.end, []).append(note.text)
    leading = [*intro, *added.pop(-1, [])]
    text = pgn
    for at in sorted(added, reverse=True):
        text = f"{text[:at]} {_comments(added[at])}{text[at:]}"
    if leading:
        text = f"{text[:first]}{_comments(leading)} {text[first:]}"
    return text
