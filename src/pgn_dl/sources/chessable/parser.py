"""Parse Chessable's getCourse/getGame responses into the core move tree.

The getGame schema (see the source's package docstring for the wider picture):

- ``game.data`` is a flat array of mainline plies, in order; the array *is* the
  mainline (``mid`` groups plies by full move and is not a parent pointer).
- Each ply carries ``before`` and ``after``, JSON strings of
  ``{"before":FEN,"after":FEN,"data":[...]}``. The ``data`` list is the ply's
  annotations in the MoveTrainer annotation shape: ``C`` entries carry prose,
  ``V`` entries carry annotation blocks whose ``S`` entries are single SAN
  moves; ``before`` applies before the move, ``after`` after it.
- ``ann1`` is an evaluation glyph (``?``, ``±``, ...); ``ann2`` its prose.
- ``draws`` are board drawings: circles (highlights) and arrows.
- A leading ply with ``san == "--"`` is a null move carrying the variation's
  prose introduction rather than a real move. A ``--`` anywhere else is a null
  move that passes the turn.
- A field with no value may come as null rather than be left out, as ``ann1``
  does on a move without a glyph.

Annotation blocks are packed into comments as move-number-tagged SAN, the same
shape chess.com courses use; the format's semantics live in the ``movetrainer``
package and everything converges on ``core.models``.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

import chess

from ...core.fetch import FetchError
from ...core.models import Arrow, Highlight, MoveNode
from ...movetrainer import (
    Block,
    DecodedPly,
    UnplayableMoveError,
    build_tree,
    glyph_nags,
    parse_annotation_data,
    strip_html,
)

# Rich-text comments

# Chessable wraps parenthetical asides in these; they render as ordinary
# brackets. Also used bare, as a numbered-list "1)" marker, so each is mapped on
# its own rather than only in pairs.
_MARKERS = {
    "@@StartBracket@@": "(",
    "@@EndBracket@@": ")",
    "@@StartBlockQuote@@": "",
    "@@EndBlockQuote@@": "",
}


def _strip_html(s: str) -> str:
    """Reduce Chessable's own markers, then the shared MoveTrainer strip."""
    for marker, repl in _MARKERS.items():
        s = s.replace(marker, repl)
    return strip_html(s)


def _comment(raw: str | None) -> tuple[str, list[Block]]:
    """Parse one node ``before``/``after`` blob into ``(text, blocks)``."""
    if not raw:
        return "", []
    try:
        blob = json.loads(raw)
    except ValueError:
        return _strip_html(raw), []
    if not isinstance(blob, dict):
        return "", []
    return parse_annotation_data(blob.get("data", []), clean_text=_strip_html)


# Drawings

# Chessable's four drawing colours map to the standard PGN %cal/%csl colours,
# carried through core.models' free-text ``type`` field. Blue has no equivalent
# among chess.com's importance levels, which is why core.pgn learned the colour
# names.
_DRAW_COLOR = {"g": "green", "y": "yellow", "r": "red", "b": "blue"}


def _parse_draws(draws: list[dict]) -> tuple[list[Arrow], list[Highlight]]:
    arrows: list[Arrow] = []
    highlights: list[Highlight] = []
    for d in draws:
        color = _DRAW_COLOR.get(d.get("color", ""), "green")
        if d.get("object") == "arrow":
            arrows.append(Arrow(from_square=d["start"], to_square=d["end"], type=color))
        elif d.get("object") == "circle":
            highlights.append(Highlight(square=d["start"], type=color))
    return arrows, highlights


# getGame


class GameContent(NamedTuple):
    oid: str
    starting_fen: str
    description: str
    result: str
    root: MoveNode


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


def parse_game(raw: dict[str, Any]) -> GameContent:
    """Convert a ``getGame`` response into one variation's move tree."""
    try:
        game = raw["game"]
        plies = game["data"]
    except (KeyError, TypeError) as exc:
        raise FetchError(
            f"chessable getGame response has no game data: {exc!r}"
        ) from None
    if not isinstance(plies, list):
        raise FetchError("chessable getGame data is not a list")

    starting_fen = game.get("initial") or chess.STARTING_FEN
    oid = str(game.get("oid", ""))

    # A leading null move carries the variation's prose introduction, not a move.
    description = ""
    if plies and plies[0].get("san") == "--":
        pre, _ = _comment(plies[0].get("before"))
        post, _ = _comment(plies[0].get("after"))
        description = _join(pre, post)
        plies = plies[1:]

    try:
        board = chess.Board(fen=starting_fen)
    except ValueError as exc:
        raise FetchError(f"chessable variation {oid} has an unreadable position: {exc}")

    decoded: list[DecodedPly] = []
    for ply in plies:
        pre_text, pre_blocks = _comment(ply.get("before"))
        post_text, post_blocks = _comment(ply.get("after"))
        arrows, highlights = _parse_draws(ply.get("draws") or [])
        decoded.append(
            DecodedPly(
                san=ply.get("san", ""),
                pre_comment=pre_text,
                post_comment=post_text,
                pre_blocks=pre_blocks,
                post_blocks=post_blocks,
                nags=glyph_nags(ply.get("ann1") or ""),
                arrows=arrows,
                highlights=highlights,
            )
        )

    try:
        root = build_tree(board, decoded)
    except UnplayableMoveError as exc:
        # Stopping at the bad ply would leave a complete-looking short game the
        # runner writes and every later run skips as done.
        raise FetchError(
            f"chessable variation {oid} has an unplayable move {exc.san!r}: {exc.reason}"
        ) from None

    return GameContent(
        oid=oid,
        starting_fen=starting_fen,
        description=description,
        result=str(game.get("result") or "*"),
        root=root,
    )


# getCourse


class Variation(NamedTuple):
    oid: str
    name: str


class Chapter(NamedTuple):
    title: str
    variations: list[Variation]


class CourseOutline(NamedTuple):
    name: str
    owned: bool
    chapters: list[Chapter]


def parse_course(raw: dict[str, Any]) -> CourseOutline:
    """Parse a ``getCourse`` response into the course outline."""
    try:
        course = raw["course"]
        chapters_raw = course["data"]
    except (KeyError, TypeError) as exc:
        raise FetchError(
            f"chessable getCourse response is not in the expected shape: {exc!r}"
        ) from None
    if not isinstance(chapters_raw, list):
        raise FetchError("chessable getCourse chapters are not a list")

    chapters: list[Chapter] = []
    for ch in chapters_raw:
        variations = [
            Variation(oid=str(v["oid"]), name=str(v.get("name") or ""))
            for v in ch.get("variations") or []
            if "oid" in v
        ]
        chapters.append(
            Chapter(title=str(ch.get("title") or ""), variations=variations)
        )

    return CourseOutline(
        name=str(course.get("name") or ""),
        owned=bool(course.get("owned")),
        chapters=chapters,
    )
