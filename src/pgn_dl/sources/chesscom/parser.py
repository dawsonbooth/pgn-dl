"""Parse chess.com lesson RPC responses into the core move tree.

The lesson schema, as returned by ``GetCourseTrainingForLearning``:

- ``plies[]`` is a flat array of mainline moves (alternating colors).
- A ply may have ``preComment`` and/or ``postComment`` strings. Both are
  JSON-encoded ``{"data":[...]}`` blocks where each entry is one of:
  - ``{"key":"C","val":"<html>"}`` - a text/HTML comment
  - ``{"key":"V","val":[ply-entries...]}`` - an inline sub-variation
  - ``{"key":"S","val":"9.Ne5?!"}`` - a single SAN move (only inside V). Its
    move number is the only thing that says where the variation belongs: which
    ply it branches from, and where one block's several lines divide.
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

from ...core.models import Arrow, Highlight, MoveNode

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
# A "<" that opens nothing a tag could close. HTMLParser reads one as the start
# of a tag and eats the rest of the comment looking for its ">", and chess prose
# compares things ("a<b in the pawn race").
_BARE_LT = re.compile(r"<(?![a-zA-Z/!][^<>]*>)")
# Horizontal whitespace only: the line breaks above are the paragraph structure.
_SPACES = re.compile(r"[^\S\n]+")
_LINE_EDGES = re.compile(r" *\n *")
_BLANK_LINES = re.compile(r"\n{3,}")


def _strip_html(s: str) -> str:
    p = _HTMLStripper()
    p.feed(_BARE_LT.sub("&lt;", s))
    # feed() holds back whatever might still be the start of a tag or an entity;
    # close() releases it.
    p.close()
    text = "".join(p.out)
    # SAN markers: keep the move, drop the markers.
    text = _SAN_MARKER.sub(r"\1", text)
    text = _SPACES.sub(" ", text)
    text = _LINE_EDGES.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


# Move-number prefix on inline variation SAN, e.g. "9.Ne5?!" or "11...g6". One
# dot is White's move, three are Black's; nothing else distinguishes the two.
_MOVE_NUM = re.compile(r"^\s*(\d+)(\.+)\s*")

# Trailing annotation glyphs, in both the figurine and the ASCII spellings
# chess.com writes. A glyph stripped off the SAN and mapped to nothing would be
# an annotation deleted from the lesson, so the patterns below are built from
# this map: nothing else can match.
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
    "+/=": 14,
    "⩱": 15,
    "=/+": 15,
    "±": 16,
    "+/-": 16,
    "∓": 17,
    "-/+": 17,
    "+-": 18,
    "-+": 19,
}

# Longest first, so "!?" reads as one glyph rather than two and "+/-" is not cut
# short at "+". A run is any sequence of them: chess.com writes "!?!" and "=∞".
_NAG_GLYPH = re.compile(
    "|".join(re.escape(g) for g in sorted(_NAG_MAP, key=len, reverse=True))
)
_NAG_SUFFIX = re.compile(rf"((?:{_NAG_GLYPH.pattern})+)\s*$")


def _strip_san(raw: str) -> tuple[str, list[int]]:
    """Strip move number and trailing NAG glyphs; return (clean_san, nags).

    A glyph the map does not know stays on the SAN, where it fails to parse
    rather than disappearing.
    """
    s = _MOVE_NUM.sub("", raw).strip()
    m = _NAG_SUFFIX.search(s)
    if m is None:
        return s, []
    return s[: m.start()].strip(), [_NAG_MAP[g] for g in _NAG_GLYPH.findall(m.group(1))]


class _VarMove(NamedTuple):
    san: str
    nags: list[int]
    raw: str = ""  # as chess.com wrote it, move number and glyphs included


class _VarComment(NamedTuple):
    text: str


# One entry of an inline variation: a move, or a comment on the move before it.
VarItem = _VarMove | _VarComment


def _attach_glyphs(block: list[VarItem], raw: str, nags: list[int]) -> None:
    """Put a glyph-only entry's NAGs on the move it annotates.

    An ``S`` entry that strips to no move at all held nothing but an evaluation
    glyph, so it annotates the entry before it. With no move before it there is
    nothing to annotate and the glyph is kept as text, because a glyph that is
    neither a NAG nor prose is an annotation deleted from the lesson.
    """
    for i in reversed(range(len(block))):
        item = block[i]
        if isinstance(item, _VarMove):
            block[i] = item._replace(nags=[*item.nags, *nags])
            return
    block.append(_VarComment(raw))


def _parse_comment(raw: str | None) -> tuple[str, list[list[VarItem]]]:
    """Return ``(text, variations)``, where each variation is a flat list of
    :class:`_VarMove` / :class:`_VarComment` items in order."""
    if not raw:
        return "", []
    try:
        doc = json.loads(raw)
    except ValueError:
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
                        block.append(_VarMove(san, nags, vv.strip()))
                    elif nags:
                        _attach_glyphs(block, vv.strip(), nags)
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


def _block_text(block: list[VarItem]) -> str:
    """A variation block as the sentence chess.com's editor composed.

    Its moves and prose alternate in reading order, move numbers included, so
    joining them recovers the author's line.
    """
    return " ".join(
        item.raw or item.san if isinstance(item, _VarMove) else item.text
        for item in block
    )


def _ply_number(raw: str) -> int | None:
    """The half-move ``raw``'s move number names, or ``None`` if it names none.

    Counted so that White's and Black's are one scale, and the same scale as
    :func:`_board_ply`: comparing the two is what places a block.
    """
    m = _MOVE_NUM.match(raw)
    if m is None:
        return None
    return 2 * int(m.group(1)) + (1 if len(m.group(2)) > 1 else 0)


def _board_ply(board: chess.Board) -> int:
    """The half-move ``board`` is waiting for, on :func:`_ply_number`'s scale."""
    return 2 * board.fullmove_number + (0 if board.turn == chess.WHITE else 1)


def _first_ply(block: list[VarItem]) -> int | None:
    """The half-move the block's first move names, if it names one."""
    first = next((i for i in block if isinstance(i, _VarMove)), None)
    return _ply_number(first.raw) if first is not None else None


class _LineStep(NamedTuple):
    board: chess.Board  # the position its move was played from
    node: MoveNode


def _build_variation_lines(
    anchor_board: chess.Board,
    block: list[VarItem],
) -> tuple[list[MoveNode], str]:
    """Turn a flat variation block into lines playable from ``anchor_board``.

    Returns ``(heads, "")``, the head MoveNode of every line the block opens at
    ``anchor_board``, or ``([], text)`` when the block is not something that can
    be played from there.

    One block is not always one line. chess.com's editor packs whatever the
    author typed into it, including several lines from one position, and nothing
    in the encoding separates them; the move number written into each SAN does.
    A number naming a position the line has already left starts a new line
    branching there, which is a sibling of the move that was played from it.

    A block that still will not replay is not corrupt, it is the lesson's
    teaching in a shape this cannot hold, so it comes back as text for the caller
    to keep on the move it annotates. All of it or none: a partial chain would be
    a line the author never wrote.
    """
    heads: list[MoveNode] = []
    # The line being built, so a move number pointing back into it says where
    # to branch.
    line: list[_LineStep] = []
    board = anchor_board.copy()
    pending = ""  # prose waiting for the move it trails
    lead = ""  # prose before the block's first move, which trails nothing

    for item in block:
        if isinstance(item, _VarComment):
            # Several comments on one move accumulate rather than clobber.
            pending = f"{pending}\n\n{item.text}" if pending else item.text
            continue

        # A trailing comment belongs to the move it followed, so it is flushed
        # before the line moves on.
        if pending:
            if line:
                line[-1].node.post_comment = pending
            else:
                lead = pending
            pending = ""

        ply = _ply_number(item.raw)
        if ply is not None and ply != _board_ply(board):
            at = next(
                (i for i, step in enumerate(line) if _board_ply(step.board) == ply), None
            )
            if at is None:
                return [], _block_text(block)
            board = line[at].board.copy()
            del line[at:]

        try:
            move = board.parse_san(item.san)
        except ValueError:
            return [], _block_text(block)
        node = MoveNode(
            san=item.san,  # keep chess.com's original spelling of the move
            nags=list(item.nags),
        )
        if line:
            line[-1].node.children.append(node)
        else:
            node.pre_comment = lead
            lead = ""
            heads.append(node)
        line.append(_LineStep(board.copy(), node))
        board.push(move)

    if not heads:
        return [], _block_text(block)  # prose, or nothing, with no move to carry it
    if pending:
        line[-1].node.post_comment = pending
    return heads, ""


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
        plies = plies[1:]

    root = MoveNode(san=None)

    board = chess.Board(fen=starting_fen)
    parent_node = root
    parent_board = board.copy()
    # Lines continuing the previous ply. They are siblings of the ply that comes
    # next, and have to be attached after it: children[0] is the mainline, and a
    # sideline there would rewrite the game.
    continuations: list[MoveNode] = []

    for ply in plies:
        san = ply.get("san", "")
        if san == "--":
            continue
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            # Stopping here would keep a complete-looking short game that the
            # runner writes, counts, and skips on every later run.
            raise ValueError(
                f"lesson {variation['id']} has an unplayable move {san!r}: {exc}"
            ) from exc

        pre_text, pre_vars = _parse_comment(ply.get("preComment"))
        post_text, post_vars = _parse_comment(ply.get("postComment"))

        board.push(move)
        position = ply.get("position", {})
        node = MoveNode(
            san=san,
            pre_comment=pre_text,
            post_comment=post_text,
            nags=_parse_nags(position),
            arrows=_parse_arrows(position),
            highlights=_parse_highlights(position),
        )
        parent_node.children.append(node)
        parent_node.children.extend(continuations)
        continuations = []

        # chess.com hangs a block off the ply it annotates without saying whether
        # it replaces that ply or continues it, and one captured lesson holds
        # both. The move number the block writes into its own SAN is what tells
        # them apart.
        for block in pre_vars + post_vars:
            if _first_ply(block) == _board_ply(board):
                # A reply to this ply, so it branches below it.
                heads, unplayable = _build_variation_lines(board, block)
                continuations.extend(heads)
            else:
                # An alternative to this ply, so it branches where the ply did.
                # A block carrying no move numbers at all lands here too: that
                # is what the encoding meant before chess.com began writing
                # them. So does one whose numbers name neither position, which
                # will not replay from here either and comes back as text -
                # anchoring it anywhere would be a guess, and a wrong guess
                # replays as moves the author never wrote.
                heads, unplayable = _build_variation_lines(parent_board, block)
                parent_node.children.extend(heads)
            if unplayable:
                node.post_comment = "\n\n".join(
                    part for part in (node.post_comment, unplayable) if part
                )

        parent_board = board.copy()
        parent_node = node

    # The last ply has no mainline move after it, so its continuations are all
    # it has: the first reads as the line's continuation and the rest as
    # variations of that, which is the only shape PGN can hold them in.
    parent_node.children.extend(continuations)

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
