"""The source-agnostic shapes every extractor produces and the writer consumes."""

from __future__ import annotations

from dataclasses import dataclass, field

import chess


@dataclass
class Arrow:
    from_square: str  # e.g. "d2"
    to_square: str
    # A key ``core.pgn._DRAWING_COLOR`` maps to a %cal/%csl colour: an importance
    # level ("primary"/"secondary"/"tertiary") or a colour name ("green"/"blue"/...).
    type: str


@dataclass
class Highlight:
    square: str
    type: str


@dataclass
class MoveNode:
    """A node in the move tree.

    ``san is None`` only for the root (the position before any move). Children
    are ordered: ``children[0]`` is the mainline continuation,
    ``children[1:]`` are alternative variations.
    """

    san: str | None
    pre_comment: str = ""
    post_comment: str = ""
    nags: list[int] = field(default_factory=list)
    arrows: list[Arrow] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)
    children: list[MoveNode] = field(default_factory=list)


@dataclass(frozen=True)
class Collection:
    """What a single invocation downloads."""

    source: str  # "chesscom"
    kind: str  # "courses"
    slug: str  # "aman-hambleton-s-london-system"
    name: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemRef:
    """Enough to compute an output path without fetching, so resume is free."""

    id: str
    path: tuple[str, ...]  # already slugified and numbered by the extractor


@dataclass
class GameItem:
    """One fetched item, as the writer consumes it."""

    headers: dict[str, str] = field(default_factory=dict)
    starting_fen: str = chess.STARTING_FEN
    comment: str = ""  # game-level comment, before the first move
    root: MoveNode = field(default_factory=lambda: MoveNode(san=None))
    pgn: str | None = None
    """Finished PGN text. When set, the runner writes it verbatim and ignores
    ``headers``/``starting_fen``/``comment``/``root``. For sources whose upstream
    format is already PGN, so nothing is gained by round-tripping it through a
    move tree."""
