"""Parse chess.com lesson RPC responses into the internal :mod:`models` tree.

The lesson schema, as returned by ``GetCourseTrainingForLearning``:

- ``plies[]`` is a flat array of mainline moves (alternating colors).
- A ply may have ``preComment`` and/or ``postComment`` strings. Both are
  JSON-encoded ``{"data":[...]}`` blocks where each entry is one of:
  - ``{"key":"C","val":"<html>"}`` - a text/HTML comment
  - ``{"key":"V","val":[ply-entries...]}`` - an inline sub-variation
  - ``{"key":"S","val":"9.Ne5?!"}`` - a single SAN move (only inside V)
- ``position.nags / arrows / highlights`` carry annotation glyphs.
- A ply with ``san == "--"`` is a null move used to attach a lesson intro;
  it's the lesson description, not a real move.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import NamedTuple

import chess

from .models import (
    Arrow,
    Author,
    Chapter,
    Course,
    Highlight,
    Lesson,
    MoveNode,
)

# Rich-text comment format


class _HTMLStripper(HTMLParser):
    """Collapse chess.com's HTML comment fragments to plain text."""

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []

    def handle_data(self, data: str) -> None:
        self.out.append(data)

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in {"br", "p"}:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self.out.append("\n")


_SAN_MARKER = re.compile(r"@@SANStart@@(.*?)@@SANEnd@@")
_WS = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    p = _HTMLStripper()
    p.feed(s)
    text = "".join(p.out)
    # SAN markers: keep the move, drop the markers.
    text = _SAN_MARKER.sub(r"\1", text)
    text = _WS.sub(" ", text)
    return text.strip()


# Move-number prefix on inline variation SAN, e.g. "9.Ne5?!" or "11...g6".
_MOVE_NUM = re.compile(r"^\s*\d+\.+\s*")

# Trailing annotation glyphs we recognise as NAGs. Every glyph the pattern can
# match needs a _NAG_MAP entry, or _strip_san would drop it without recording it.
_NAG_SUFFIX = re.compile(r"([!?]+|[±∓⩲⩱=∞]+)\s*$")
_NAG_MAP: dict[str, int] = {
    "!": 1,
    "?": 2,
    "!!": 3,
    "??": 4,
    "!?": 5,
    "?!": 6,
    "=": 10,
    "∞": 13,
    "⩲": 14,
    "⩱": 15,
    "±": 16,
    "∓": 17,
}


def _strip_san(raw: str) -> tuple[str, list[int]]:
    """Strip move number and trailing NAG glyphs; return (clean_san, nags)."""
    s = _MOVE_NUM.sub("", raw).strip()
    nags: list[int] = []
    m = _NAG_SUFFIX.search(s)
    if m:
        glyph = m.group(1).strip()
        if glyph in _NAG_MAP:
            nags.append(_NAG_MAP[glyph])
        s = s[: m.start()].strip()
    return s, nags


class _VarMove(NamedTuple):
    san: str
    nags: list[int]


class _VarComment(NamedTuple):
    text: str


# One entry of an inline variation: a move, or a comment on the move before it.
VarItem = _VarMove | _VarComment


def _parse_comment(raw: str | None) -> tuple[str, list[list[VarItem]]]:
    """Return ``(text, variations)``, where each variation is a flat list of
    :class:`_VarMove` / :class:`_VarComment` items in order."""
    if not raw:
        return "", []
    try:
        doc = json.loads(raw)
    except TypeError, ValueError:
        # Not all comments are JSON; some are plain strings.
        return _strip_html(raw), []

    text_parts: list[str] = []
    variations: list[list[VarItem]] = []
    for entry in doc.get("data", []):
        key = entry.get("key")
        val = entry.get("val")
        if key == "C" and isinstance(val, str):
            t = _strip_html(val)
            if t:
                text_parts.append(t)
        elif key == "V" and isinstance(val, list):
            block: list[VarItem] = []
            for v in val:
                vk = v.get("key")
                vv = v.get("val")
                if vk == "S" and isinstance(vv, str):
                    san, nags = _strip_san(vv)
                    if san:
                        block.append(_VarMove(san, nags))
                elif vk == "C" and isinstance(vv, str):
                    trail = _strip_html(vv)
                    if trail:
                        block.append(_VarComment(trail))
            if block:
                variations.append(block)
    return "\n\n".join(text_parts), variations


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


def _build_variation_branch(
    parent_board: chess.Board,
    block: list[VarItem],
) -> MoveNode | None:
    """Turn a flat variation block into a chain of MoveNode siblings.

    ``parent_board`` is the position *before* the variation's first move.
    Returns the head MoveNode of the chain (= the alternative move), or
    ``None`` if no SAN move could be parsed.
    """
    head: MoveNode | None = None
    cursor: MoveNode | None = None
    board = parent_board.copy()
    pending_trailing: str = ""

    for item in block:
        if isinstance(item, _VarMove):
            # A trailing comment belongs to the move it followed, so flush any
            # pending text onto the current cursor before advancing.
            if pending_trailing and cursor is not None:
                cursor.post_comment = pending_trailing
                pending_trailing = ""
            try:
                move = board.parse_san(item.san)
            except chess.IllegalMoveError, chess.AmbiguousMoveError, ValueError:
                # Give up on this variation if SAN doesn't parse cleanly.
                return None
            board.push(move)
            node = MoveNode(
                san=item.san,  # keep chess.com's original spelling of the move
                color="COLOR_WHITE" if board.turn == chess.BLACK else "COLOR_BLACK",
                nags=list(item.nags),
            )
            if cursor is None:
                head = node
            else:
                cursor.children.append(node)
            cursor = node
        else:  # _VarComment: multiple on one move accumulate rather than clobber.
            pending_trailing = (
                f"{pending_trailing}\n\n{item.text}" if pending_trailing else item.text
            )

    if cursor is not None and pending_trailing:
        cursor.post_comment = pending_trailing
    return head


def parse_lesson(raw: dict, idx: int) -> Lesson:
    """Convert a ``GetCourseTrainingForLearning`` response to a :class:`Lesson`."""
    lesson_raw = raw["lesson"]
    variation = lesson_raw["variation"]
    starting_fen = variation["startingPosition"]["fen"]
    plies = lesson_raw.get("plies", [])

    # Drop a leading null-move ply ("--") and use its preComment as description.
    description = ""
    if plies and plies[0].get("san") == "--":
        intro_text, _ = _parse_comment(plies[0].get("preComment"))
        description = intro_text
        plies = plies[1:]

    root = MoveNode(san=None, color=None)

    board = chess.Board(fen=starting_fen)
    parent_node = root
    parent_board = board.copy()

    for ply in plies:
        san = ply.get("san", "")
        if san == "--":
            continue
        try:
            move = board.parse_san(san)
        except chess.IllegalMoveError, chess.AmbiguousMoveError, ValueError:
            # If SAN won't parse, stop here; we keep what we have.
            break

        pre_text, pre_vars = _parse_comment(ply.get("preComment"))
        post_text, post_vars = _parse_comment(ply.get("postComment"))

        # Variations are alternatives to this ply, so they branch at the parent.
        variation_blocks = pre_vars + post_vars

        board.push(move)
        position = ply.get("position", {})
        node = MoveNode(
            san=san,
            color=ply.get("color"),
            pre_comment=pre_text,
            post_comment=post_text,
            nags=_parse_nags(position),
            arrows=_parse_arrows(position),
            highlights=_parse_highlights(position),
        )
        parent_node.children.append(node)

        for block in variation_blocks:
            branch_head = _build_variation_branch(parent_board, block)
            if branch_head is not None:
                parent_node.children.append(branch_head)

        parent_board = board.copy()
        parent_node = node

    return Lesson(
        id=variation["id"],
        idx=idx,
        name=variation["name"],
        type=variation.get("type", ""),
        starting_fen=starting_fen,
        learner_color=variation.get("learnerColor"),
        description=description,
        root=root,
    )


# Course builder


def parse_course_skeleton(raw_session: dict) -> tuple[Course, list[dict]]:
    """Parse the ``GetCourseLearningSession`` response.

    Returns the :class:`Course` (with empty lessons in each chapter) and the
    raw ``lessonsHeaders`` list in chess.com's order so the caller knows what
    to fetch and how to number lessons.
    """
    ls = raw_session["learningSession"]
    course_info = ls["moveTrainers"][0]
    chapters_raw = course_info.get("chapters", [])
    authors_raw = course_info.get("authors", [])
    lessons_headers = ls.get("lessonsHeaders", [])

    chapters: list[Chapter] = []
    for i, c in enumerate(chapters_raw, start=1):
        chapters.append(Chapter(id=c["id"], idx=i, name=c.get("name", f"Chapter {i}")))

    authors = [
        Author(name=a.get("name", "?"), chess_title=a.get("chessTitle", ""))
        for a in authors_raw
    ]

    course = Course(
        id=course_info["id"],
        slug=course_info.get("slug", ""),
        name=course_info.get("name", course_info.get("slug", "")),
        authors=authors,
        chapters=chapters,
    )
    return course, lessons_headers
