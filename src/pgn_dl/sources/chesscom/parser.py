"""Parse chess.com lesson RPC responses into the core move tree.

The lesson schema, as returned by ``GetCourseTrainingForLearning``:

- ``plies[]`` is a flat array of mainline moves, each carrying its ``color`` and
  the ``position`` after it.
- A ply may have ``preComment`` and/or ``postComment`` strings. Both are
  JSON-encoded ``{"data":[...]}`` blocks in the MoveTrainer annotation shape:
  ``C`` entries carry prose, ``V`` entries carry annotation blocks whose ``S``
  entries are single SAN moves. The move number written into each SAN is the
  only thing that says where a block belongs.
- ``position.nags / arrows / highlights`` carry annotation glyphs.
- A leading ply with ``san == "--"`` carries the lesson intro, which becomes the
  description. When the ply after it is the other side's, it is also a pass, and
  the lesson starts from the position chess.com gives after it. Anywhere else
  ``"--"`` is a null move that passes the turn.

The format's semantics - the block item model, the glyph vocabulary, block
anchoring, and the mainline replay walk - live in the ``movetrainer`` package;
this file decodes the RPC envelope into its decoded-ply form.
"""

from __future__ import annotations

import json
from typing import NamedTuple

import chess

from ...core.models import Arrow, Highlight, MoveNode
from ...movetrainer import (
    Block,
    DecodedPly,
    UnplayableMoveError,
    build_tree,
    parse_annotation_data,
    strip_html,
)

# Rich-text comment format


def _parse_comment(raw: str | None) -> tuple[str, list[Block]]:
    """Return ``(text, blocks)`` decoded from one comment carrier."""
    if not raw:
        return "", []
    try:
        doc = json.loads(raw)
    except ValueError:
        # Not all comments are JSON; some are plain strings.
        return strip_html(raw), []
    return parse_annotation_data(doc.get("data", []))


# Position annotations (arrows, highlights, NAGs)


def _square_name(s: str) -> str:
    # "SQUARE_E4" -> "e4"
    return s.removeprefix("SQUARE_").lower()


_ARROW_TYPE = {
    "COURSE_DRAWING_ARROW_TYPE_PRIMARY": "primary",
    "COURSE_DRAWING_ARROW_TYPE_SECONDARY": "secondary",
    "COURSE_DRAWING_ARROW_TYPE_TERTIARY": "tertiary",
}
_SQUARE_TYPE = {
    "COURSE_DRAWING_SQUARE_TYPE_PRIMARY": "primary",
    "COURSE_DRAWING_SQUARE_TYPE_SECONDARY": "secondary",
    "COURSE_DRAWING_SQUARE_TYPE_TERTIARY": "tertiary",
}


def _parse_arrows(pos: dict) -> list[Arrow]:
    return [
        Arrow(
            from_square=_square_name(a["squareFrom"]),
            to_square=_square_name(a["squareTo"]),
            type=_ARROW_TYPE.get(a.get("type", ""), "primary"),
        )
        for a in pos.get("arrows", [])
    ]


def _parse_highlights(pos: dict) -> list[Highlight]:
    return [
        Highlight(
            square=_square_name(h["square"]),
            type=_SQUARE_TYPE.get(h.get("type", ""), "primary"),
        )
        for h in pos.get("highlights", [])
    ]


def _parse_nags(pos: dict) -> list[int]:
    return [int(n["nagId"]) for n in pos.get("nags", []) if "nagId" in n]


# Lesson tree builder


class LessonContent(NamedTuple):
    id: str
    starting_fen: str
    description: str
    root: MoveNode


def parse_lesson_tree(raw: dict) -> LessonContent:
    """Convert a ``GetCourseTrainingForLearning`` response to lesson content."""
    lesson_raw = raw["lesson"]
    variation = lesson_raw["variation"]
    starting_fen = variation["startingPosition"]["fen"]
    plies = lesson_raw.get("plies", [])

    # Drop a leading null-move ply ("--") and use its preComment as description.
    description = ""
    if plies and plies[0].get("san") == "--":
        intro_text, _ = _parse_comment(plies[0].get("preComment"))
        description = intro_text
        # Followed by the other side's ply, it is a pass as well, and the lesson
        # starts from chess.com's own position after it.
        next_color = plies[1].get("color") if len(plies) > 1 else None
        if next_color is not None and next_color != plies[0].get("color"):
            starting_fen = plies[0]["position"]["fen"]
        plies = plies[1:]

    decoded: list[DecodedPly] = []
    for ply in plies:
        pre_text, pre_blocks = _parse_comment(ply.get("preComment"))
        post_text, post_blocks = _parse_comment(ply.get("postComment"))
        position = ply.get("position", {})
        decoded.append(
            DecodedPly(
                san=ply.get("san", ""),
                pre_comment=pre_text,
                post_comment=post_text,
                pre_blocks=pre_blocks,
                post_blocks=post_blocks,
                nags=_parse_nags(position),
                arrows=_parse_arrows(position),
                highlights=_parse_highlights(position),
            )
        )

    try:
        root = build_tree(chess.Board(fen=starting_fen), decoded)
    except UnplayableMoveError as exc:
        # Stopping at the bad ply would keep a complete-looking short game that
        # the runner writes, counts, and skips on every later run.
        raise ValueError(
            f"lesson {variation['id']} has an unplayable move {exc.san!r}: {exc.reason}"
        ) from exc

    return LessonContent(
        id=variation["id"],
        starting_fen=starting_fen,
        description=description,
        root=root,
    )


# Course builder


class CourseInfo(NamedTuple):
    slug: str
    name: str
    author: str  # first author's name, or "" - becomes the Annotator header
    chapters: list[tuple[str, str]]  # (chapter id, name) in course order


def parse_course_skeleton(raw_session: dict) -> tuple[CourseInfo, list[dict]]:
    """Parse the ``GetCourseLearningSession`` response.

    Returns the :class:`CourseInfo` and the raw ``lessonsHeaders`` list in
    chess.com's order so the caller knows what to fetch and how to number
    lessons.
    """
    ls = raw_session["learningSession"]
    course_info = ls["moveTrainers"][0]
    authors_raw = course_info.get("authors", [])
    chapters = [
        (c["id"], c.get("name", f"Chapter {i}"))
        for i, c in enumerate(course_info.get("chapters", []), start=1)
    ]
    info = CourseInfo(
        slug=course_info.get("slug", ""),
        name=course_info.get("name", course_info.get("slug", "")),
        author=authors_raw[0].get("name", "") if authors_raw else "",
        chapters=chapters,
    )
    return info, ls.get("lessonsHeaders", [])
