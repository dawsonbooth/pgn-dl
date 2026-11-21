"""A chessendings drill page as a game, and the helpers the other kinds share.

A Theory lesson and a Strategy position are both drill pages: a starting
position, the side the student plays (always the side to move), a goal (win or
draw), a title and an introduction. A Theory lesson adds a walk-through, a list
of steps, each the student's ``move`` and the defender's ``reply`` (UCI, either
may be absent) followed by the coach's ``comment``, with drawings on the board
and sometimes a ``demo``: a line played out from the position after the step's
own moves. A Strategy position has no steps.

The moves and replies are the mainline. Each comment and drawing sits on the
last move its step played, or in the game comment for a step before any move.
Each demo is merged in as a variation from where it starts, following the
moves already there and branching where it departs, so a demo that opens with
the next mainline move does not repeat it. A demo after the lesson's last move
has nothing to branch from, so it continues the mainline; on the site those
demos play out the rest of the line.

Drawings take the site's colours: marked squares, gold on the site, are
yellow; a zone (a rectangle the coach shades) becomes its squares in blue; an
arrow is blue where the site draws a line a piece watches and green otherwise.
A square both marked and shaded keeps the mark.

The site skips a move it cannot play. Here such a move fails the item: a lesson
written short would be counted done and never retried.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import chess

from ...core.fetch import FetchError
from ...core.models import Arrow, GameItem, Highlight, MoveNode
from .client import SITE

_SIDES = {"w": chess.WHITE, "b": chess.BLACK}
_GOALS = ("win", "draw")
_MARK, _ZONE = "yellow", "blue"


@dataclass(frozen=True)
class Drawings:
    """What the site draws on one position."""

    squares: tuple[str, ...] = ()
    zones: tuple[tuple[str, str], ...] = ()  # opposite corners
    arrows: tuple[tuple[str, str, str], ...] = ()  # from, to, the site's kind


@dataclass(frozen=True)
class Step:
    comment: str
    move: str | None
    reply: str | None
    drawings: Drawings
    demo: tuple[str, ...]


@dataclass(frozen=True)
class Drill:
    title: str
    intro: str
    intro_squares: tuple[str, ...]
    fen: str
    hero: chess.Color
    goal: str
    steps: tuple[Step, ...]


def task(color: chess.Color, goal: str) -> str:
    """A position's task as the site words it: "Black to play and draw"."""
    side = "White" if color == chess.WHITE else "Black"
    return f"{side} to play and {goal}"


def players(name: str, color: chess.Color) -> dict[str, str]:
    """The player headers: ``name`` on the side the position is played from."""
    if color == chess.WHITE:
        return {"White": name, "Black": "?"}
    return {"White": "?", "Black": name}


def paragraphs(*parts: str) -> str:
    """Comment text, one paragraph per non-empty part."""
    return "\n\n".join(part for part in parts if part)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _squares(value: object, subject: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(s, str) and s in chess.SQUARE_NAMES for s in value
    ):
        raise FetchError(f"{subject} marks something that is not a square")
    return tuple(value)


def _pairs(value: object, subject: str) -> list[Mapping[str, Any]]:
    """A list of ``{"from", "to"}`` objects naming two squares each."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(p, Mapping)
        and p.get("from") in chess.SQUARE_NAMES
        and p.get("to") in chess.SQUARE_NAMES
        for p in value
    ):
        raise FetchError(f"{subject} draws between things that are not squares")
    return value


def read_drawings(raw: Mapping[str, Any], subject: str) -> Drawings:
    """The ``highlightSquares``, ``zones`` and ``arrows`` of a step or frame."""
    return Drawings(
        squares=_squares(raw.get("highlightSquares"), subject),
        zones=tuple((z["from"], z["to"]) for z in _pairs(raw.get("zones"), subject)),
        arrows=tuple(
            (a["from"], a["to"], str(a.get("kind") or ""))
            for a in _pairs(raw.get("arrows"), subject)
        ),
    )


def _zone_squares(corner: str, other: str) -> list[str]:
    a, b = chess.parse_square(corner), chess.parse_square(other)
    files = range(
        min(chess.square_file(a), chess.square_file(b)),
        max(chess.square_file(a), chess.square_file(b)) + 1,
    )
    ranks = range(
        min(chess.square_rank(a), chess.square_rank(b)),
        max(chess.square_rank(a), chess.square_rank(b)) + 1,
    )
    return [chess.square_name(chess.square(f, r)) for r in ranks for f in files]


def add_drawings(node: MoveNode, drawings: Drawings) -> None:
    """Put ``drawings`` on ``node``, a mark outranking a zone on one square."""
    marked = {h.square: h for h in node.highlights}
    for square in drawings.squares:
        if square in marked:
            marked[square].type = _MARK
        else:
            marked[square] = Highlight(square=square, type=_MARK)
            node.highlights.append(marked[square])
    for corner, other in drawings.zones:
        for square in _zone_squares(corner, other):
            if square not in marked:
                marked[square] = Highlight(square=square, type=_ZONE)
                node.highlights.append(marked[square])
    for start, end, kind in drawings.arrows:
        color = "blue" if kind == "line" else "green"
        node.arrows.append(Arrow(from_square=start, to_square=end, type=color))


def _uci(value: object, subject: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise FetchError(f"{subject} has a move that is not a UCI string")
    return value


def _step(raw: object, subject: str) -> Step:
    if not isinstance(raw, Mapping):
        raise FetchError(f"{subject} is not an object")
    demo = raw.get("demo")
    moves = demo.get("moves") if isinstance(demo, Mapping) else None
    if demo is not None and not (
        isinstance(moves, list) and all(isinstance(m, str) and m for m in moves)
    ):
        raise FetchError(f"{subject} has a demo without a list of moves")
    return Step(
        comment=_text(raw.get("comment")),
        move=_uci(raw.get("move"), subject),
        reply=_uci(raw.get("reply"), subject),
        drawings=read_drawings(raw, subject),
        demo=tuple(moves or ()),
    )


def parse_drill(props: Mapping[str, Any], subject: str) -> Drill:
    """A drill page's props, read and checked.

    ``subject`` names the drill in an error message. A step's other keys, such
    as ``notTheFastestMate``, are notes to the site's own player and are not
    read.
    """
    title, fen = props.get("title"), props.get("initialFen")
    hero, goal = props.get("initialHeroColor"), props.get("goal")
    if not isinstance(title, str) or not title.strip():
        raise FetchError(f"{subject} has no title")
    if not isinstance(fen, str):
        raise FetchError(f"{subject} has no starting position")
    try:
        board = chess.Board(fen)
    except ValueError:
        raise FetchError(f"{subject} has an unreadable starting position") from None
    if hero not in _SIDES or goal not in _GOALS:
        raise FetchError(f"{subject} has no side to play or no goal")
    if _SIDES[hero] != board.turn:
        # Every page is played from the side to move; one that is not would
        # put the student's name on the wrong side.
        raise FetchError(f"{subject} is played by the side not to move")
    steps = props.get("lesson")
    if steps is not None and not isinstance(steps, list):
        raise FetchError(f"{subject} has a lesson that is not a list of steps")
    return Drill(
        title=title.strip(),
        intro=_text(props.get("intro")),
        intro_squares=_squares(props.get("introHighlightSquares"), subject),
        fen=fen,
        hero=_SIDES[hero],
        goal=goal,
        steps=tuple(
            _step(raw, f"{subject} step {n}")
            for n, raw in enumerate(steps or (), start=1)
        ),
    )


def _play(
    node: MoveNode, board: chess.Board, uci: str, subject: str
) -> tuple[MoveNode, chess.Board]:
    """``uci`` played from ``node``: the child it reaches, made if new."""
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        raise FetchError(f"{subject} has an unreadable move {uci!r}") from None
    if move not in board.legal_moves:
        raise FetchError(f"{subject} plays {uci}, illegal in {board.fen()}")
    san = board.san(move)
    child = next((c for c in node.children if c.san == san), None)
    if child is None:
        child = MoveNode(san=san)
        node.children.append(child)
    after = board.copy(stack=False)
    after.push(move)
    return child, after


def drill_game(
    drill: Drill,
    *,
    subject: str,
    headers: Mapping[str, str],
    preface: str = "",
    essay: str = "",
) -> GameItem:
    """``drill`` as one game under ``headers`` and the drill's own.

    The game comment is the task, ``preface``, the introduction, ``essay``,
    then the comments of any steps before the first move.
    """
    root = MoveNode(san=None)
    add_drawings(root, Drawings(squares=drill.intro_squares))
    opening = [f"{task(drill.hero, drill.goal)}.", preface, drill.intro, essay]
    node, board = root, chess.Board(drill.fen)
    demos: list[tuple[MoveNode, chess.Board, Step, str]] = []
    for number, step in enumerate(drill.steps, start=1):
        where = f"{subject} step {number}"
        for uci in (step.move, step.reply):
            if uci is not None:
                node, board = _play(node, board, uci, where)
        if node is root:
            opening.append(step.comment)
        else:
            node.post_comment = paragraphs(node.post_comment, step.comment)
        add_drawings(node, step.drawings)
        if step.demo:
            demos.append((node, board, step, where))
    # After the whole mainline is in, so a demo never takes its place.
    for node, board, step, where in demos:
        for uci in step.demo:
            node, board = _play(node, board, uci, f"{where} demo")
    return GameItem(
        headers={
            "Event": drill.title,
            "Site": SITE,
            **players(drill.title, drill.hero),
            **headers,
        },
        starting_fen=drill.fen,
        comment=paragraphs(*opening),
        root=root,
    )
