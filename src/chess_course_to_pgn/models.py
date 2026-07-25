"""The internal course tree that :mod:`parser` builds and :mod:`pgn` serialises.

RPC responses stay as plain ``dict``s in :mod:`parser`; these dataclasses are
the stable shape everything downstream works against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Color = Literal["COLOR_WHITE", "COLOR_BLACK"]


@dataclass
class Arrow:
    from_square: str  # e.g. "d2"
    to_square: str
    type: str  # "primary" | "secondary" | "tertiary"


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
    color: Color | None
    pre_comment: str = ""
    post_comment: str = ""
    nags: list[int] = field(default_factory=list)
    arrows: list[Arrow] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)
    children: list[MoveNode] = field(default_factory=list)


@dataclass
class Lesson:
    id: str
    idx: int  # 1-based within chapter
    name: str
    type: str  # COURSE_VARIATION_TYPE_LESSON / _INFORMATIONAL / _QUIZ / ...
    starting_fen: str
    learner_color: Color | None
    description: str  # intro text from the null-move ply, if any
    root: MoveNode


@dataclass
class Chapter:
    id: str
    idx: int  # 1-based
    name: str
    lessons: list[Lesson] = field(default_factory=list)


@dataclass
class Author:
    name: str
    chess_title: str = ""


@dataclass
class Course:
    id: str
    slug: str
    name: str
    authors: list[Author]
    chapters: list[Chapter]
