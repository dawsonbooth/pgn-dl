"""Serialize a :class:`GameItem` to PGN text via python-chess.

Annotation extensions in comments (Lichess/ChessBase %cal / %csl):
- Arrows  -> [%cal Gd2d4,Re1e4,...]
- Squares -> [%csl Re4,Gf6,...]
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable

import chess
import chess.pgn

from .models import Arrow, Collection, GameItem, Highlight, MoveNode

# The three drawing levels map to Lichess/ChessBase colours.
_DRAWING_COLOR = {"primary": "G", "secondary": "Y", "tertiary": "R"}

_DEFAULT_HEADERS = {
    "White": "?",
    "Black": "?",
    "Date": "????.??.??",
    "Result": "*",
}

#: PGN wraps a comment in braces and offers no way to escape one inside it, so a
#: brace in the text cannot be written as itself. python-chess's exporter drops
#: a ``}`` and keeps a ``{``, leaving a valid file that re-parses without errors
#: and has quietly lost characters. Upstream comments are prose and markdown,
#: where a brace is ordinary, so substitute the visually nearest bracket: that
#: keeps every character and stays balanced.
_BRACES = str.maketrans({"{": "(", "}": ")"})


def _comment_text(text: str) -> str:
    """One comment's text, made safe to sit between PGN's own braces."""
    return text.translate(_BRACES)


#: Header values are upstream free text in every source. PGN does define an
#: escape for the two characters that end a value early, so unlike a brace in a
#: comment nothing has to be substituted away. A line break has no escape: a
#: header is one line, and the rest of the value would start a second game that
#: re-reads as a complete one. It collapses to a space, as does any other
#: control character.
_HEADER_ESCAPE = str.maketrans({"\\": "\\\\", '"': '\\"'})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def header_value(value: str) -> str:
    """One header value, made safe to sit between PGN's own quotes."""
    return _CONTROL.sub(" ", value).translate(_HEADER_ESCAPE).strip()


def reexport_pgn(
    pgn: str,
    headers: dict[str, str],
    *,
    subject: str,
    error: Callable[[str], Exception],
    transform: Callable[[chess.pgn.Game], None] | None = None,
) -> str:
    """Re-export upstream PGN text under our headers, keeping everything else.

    For sources whose material arrives as PGN already. ``subject`` names the
    item in an error message, ``error`` builds the exception the caller's
    runner path expects, and ``transform`` may rewrite the parsed game before
    it is exported.
    """
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise error(f"{subject} is not readable as PGN")
    if game.errors:
        # python-chess does not stop at a move it cannot play: it records the
        # error and drops that move along with everything after it, so
        # "1. e4 e5 2. Nf9 Nc6 3. Bb5 *" exports as "1. e4 e5 *", a file that
        # reads as a complete short game. The runner would count that written
        # and every later run would skip it, so the truncation would never be
        # retried. Fail the item instead.
        raise error(f"{subject} has an unreadable move: {game.errors[0]}")
    for k, v in headers.items():
        game.headers[k] = header_value(v)
    if transform is not None:
        transform(game)
    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


def _arrows_pgn(arrows: list[Arrow]) -> str:
    items = ",".join(
        f"{_DRAWING_COLOR.get(a.type, 'G')}{a.from_square}{a.to_square}" for a in arrows
    )
    return f"[%cal {items}]"


def _highlights_pgn(highlights: list[Highlight]) -> str:
    items = ",".join(f"{_DRAWING_COLOR.get(h.type, 'G')}{h.square}" for h in highlights)
    return f"[%csl {items}]"


def _build_move_comment(node: MoveNode) -> str:
    parts: list[str] = []
    if node.post_comment:
        parts.append(_comment_text(node.post_comment))
    if node.arrows:
        parts.append(_arrows_pgn(node.arrows))
    if node.highlights:
        parts.append(_highlights_pgn(node.highlights))
    return " ".join(p for p in parts if p)


def _attach_children(
    pgn_parent: chess.pgn.GameNode,
    parent_board: chess.Board,
    children: list[MoveNode],
) -> None:
    for child in children:
        # Skipping a node takes its whole subtree with it, and the shortened
        # game still writes as a file the runner counts and later runs skip, so
        # the item fails instead and is left for a re-run. Every source that
        # builds a tree from upstream SAN reaches its first board here.
        if child.san is None:
            raise ValueError("a move node below the root carries no SAN")
        try:
            move = parent_board.parse_san(child.san)
        except ValueError as exc:
            raise ValueError(
                f"{child.san!r} cannot be played in {parent_board.fen()}: {exc}"
            ) from exc

        # First child is the mainline continuation; siblings are variations.
        pgn_child = pgn_parent.add_variation(move)
        if child.pre_comment:
            pgn_child.starting_comment = _comment_text(child.pre_comment)
        comment = _build_move_comment(child)
        if comment:
            pgn_child.comment = comment
        for nag in child.nags:
            pgn_child.nags.add(nag)

        next_board = parent_board.copy()
        next_board.push(move)
        _attach_children(pgn_child, next_board, child.children)


def game_to_pgn(collection: Collection, item: GameItem) -> str:
    """Return the PGN text for one item."""
    game = chess.pgn.Game()
    headers = {**_DEFAULT_HEADERS, **collection.headers, **item.headers}
    for name, value in headers.items():
        game.headers[name] = header_value(value)

    if item.starting_fen and item.starting_fen != chess.STARTING_FEN:
        game.headers["FEN"] = item.starting_fen
        game.headers["SetUp"] = "1"

    if item.comment:
        game.comment = _comment_text(item.comment)

    board = chess.Board(fen=item.starting_fen)
    _attach_children(game, board, item.root.children)

    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)
