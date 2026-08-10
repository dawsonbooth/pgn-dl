"""Parse Chessable's getCourse/getGame responses into the core move tree.

The getGame schema (see the source's package docstring for the wider picture):

- ``game.data`` is a flat array of mainline plies, in order; the array *is* the
  mainline (``mid`` groups plies by full move and is not a parent pointer).
- Each ply carries ``before`` and ``after``, JSON strings of
  ``{"before":FEN,"after":FEN,"data":[...]}``. The ``data`` list is the ply's
  annotations: ``before`` before the move, ``after`` after it. Each entry is:
  - ``{"key":"C","val":"<html>"}`` - a text/HTML comment
  - ``{"key":"V","val":[entries...]}`` - an inline sub-variation
  - ``{"key":"S","val":"1...c5"}`` - a single SAN move (only inside ``V``); its
    move number is the only thing that says where the variation belongs.
- ``ann1`` is an evaluation glyph (``?``, ``±``, ...); ``ann2`` its prose.
- ``draws`` are board drawings: circles (highlights) and arrows.
- A leading ply with ``san == "--"`` is a null move carrying the variation's
  prose introduction rather than a real move.

Sub-variations are packed into comments as move-number-tagged SAN, the same
shape chess.com courses use; everything converges on ``core.models``.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, NamedTuple

import chess

from ...core.fetch import FetchError
from ...core.models import Arrow, Highlight, MoveNode

# Rich-text comments


class _HTMLStripper(HTMLParser):
    """Collapse Chessable's HTML comment fragments to plain text."""

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
# Chessable wraps parenthetical asides in these; they render as ordinary
# brackets. Also used bare, as a numbered-list "1)" marker, so each is mapped on
# its own rather than only in pairs.
_MARKERS = {
    "@@StartBracket@@": "(",
    "@@EndBracket@@": ")",
    "@@StartBlockQuote@@": "",
    "@@EndBlockQuote@@": "",
}
# A "<" that opens nothing a tag could close. HTMLParser would read one as the
# start of a tag and swallow the rest of the comment, and chess prose compares
# things ("a<b in the pawn race").
_BARE_LT = re.compile(r"<(?![a-zA-Z/!][^<>]*>)")
_SPACES = re.compile(r"[^\S\n]+")
_LINE_EDGES = re.compile(r" *\n *")
_BLANK_LINES = re.compile(r"\n{3,}")


def _strip_html(s: str) -> str:
    for marker, repl in _MARKERS.items():
        s = s.replace(marker, repl)
    p = _HTMLStripper()
    p.feed(_BARE_LT.sub("&lt;", s))
    # feed() holds back what might still be a tag or an entity; close() releases it.
    p.close()
    text = "".join(p.out)
    text = _SAN_MARKER.sub(r"\1", text)  # keep the move, drop the markers
    text = _SPACES.sub(" ", text)
    text = _LINE_EDGES.sub("\n", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


# Move-number prefix on inline variation SAN, e.g. "9.Ne5?!" or "11...g6". One
# dot is White's move, three are Black's; nothing else distinguishes the two.
_MOVE_NUM = re.compile(r"^\s*(\d+)(\.+)\s*")

# Trailing evaluation glyphs, figurine and ASCII, mapped to standard PGN NAGs.
# Both ``ann1`` and an inline SAN's suffix are read through this map; a glyph it
# does not know is left on the SAN, where it fails to parse rather than vanishing.
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
# short at "+".
_NAG_GLYPH = re.compile(
    "|".join(re.escape(g) for g in sorted(_NAG_MAP, key=len, reverse=True))
)
_NAG_SUFFIX = re.compile(rf"((?:{_NAG_GLYPH.pattern})+)\s*$")


def _glyph_nags(glyph: str) -> list[int]:
    """The NAGs a glyph run maps to; ``[]`` for anything not wholly recognised."""
    glyph = glyph.strip()
    # ``_NAG_SUFFIX`` requires at least one glyph, so an empty or non-glyph
    # string fails to match and yields no NAGs - no separate empty check needed.
    if _NAG_SUFFIX.fullmatch(glyph) is None:
        return []
    return [_NAG_MAP[g] for g in _NAG_GLYPH.findall(glyph)]


def _strip_san(raw: str) -> tuple[str, list[int]]:
    """Strip move number and trailing NAG glyphs; return ``(clean_san, nags)``."""
    s = _MOVE_NUM.sub("", raw).strip()
    m = _NAG_SUFFIX.search(s)
    if m is None:
        return s, []
    return s[: m.start()].strip(), [_NAG_MAP[g] for g in _NAG_GLYPH.findall(m.group(1))]


class _VarMove(NamedTuple):
    san: str
    nags: list[int]
    raw: str = ""  # as Chessable wrote it, move number and glyphs included


class _VarComment(NamedTuple):
    text: str


VarItem = _VarMove | _VarComment


def _attach_glyphs(block: list[VarItem], raw: str, nags: list[int]) -> None:
    """Put a glyph-only entry's NAGs on the move it annotates, else keep as text."""
    for i in reversed(range(len(block))):
        item = block[i]
        if isinstance(item, _VarMove):
            block[i] = item._replace(nags=[*item.nags, *nags])
            return
    block.append(_VarComment(raw))


def _parse_data(data: list[dict]) -> tuple[str, list[list[VarItem]]]:
    """Split one annotation ``data`` list into ``(text, variations)``.

    ``text`` is the concatenated prose; each variation is a flat list of
    :class:`_VarMove` / :class:`_VarComment` items in reading order.
    """
    text_parts: list[str] = []
    variations: list[list[VarItem]] = []
    for entry in data:
        key = entry.get("key")
        val = entry.get("val")
        if key == "C" and isinstance(val, str):
            if t := _strip_html(val):
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
                    if trail := _strip_html(vv):
                        block.append(_VarComment(trail))
            if block:
                variations.append(block)
    return "\n\n".join(text_parts), variations


def _comment(raw: str | None) -> tuple[str, list[list[VarItem]]]:
    """Parse one node ``before``/``after`` blob into ``(text, variations)``."""
    if not raw:
        return "", []
    try:
        blob = json.loads(raw)
    except ValueError:
        return _strip_html(raw), []
    if not isinstance(blob, dict):
        return "", []
    return _parse_data(blob.get("data", []))


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


# Variation placement


def _block_text(block: list[VarItem]) -> str:
    return " ".join(
        item.raw or item.san if isinstance(item, _VarMove) else item.text
        for item in block
    )


def _ply_number(raw: str) -> int | None:
    """The half-move ``raw``'s move number names, on :func:`_board_ply`'s scale."""
    m = _MOVE_NUM.match(raw)
    if m is None:
        return None
    return 2 * int(m.group(1)) + (1 if len(m.group(2)) > 1 else 0)


def _board_ply(board: chess.Board) -> int:
    """The half-move ``board`` is waiting for, on :func:`_ply_number`'s scale."""
    return 2 * board.fullmove_number + (0 if board.turn == chess.WHITE else 1)


def _first_ply(block: list[VarItem]) -> int | None:
    first = next((i for i in block if isinstance(i, _VarMove)), None)
    return _ply_number(first.raw) if first is not None else None


class _LineStep(NamedTuple):
    board: chess.Board
    node: MoveNode


def _build_variation_lines(
    anchor_board: chess.Board, block: list[VarItem]
) -> tuple[list[MoveNode], str]:
    """Turn a flat variation block into lines playable from ``anchor_board``.

    Returns ``(heads, "")`` - the head of every line the block opens - or
    ``([], text)`` when the block will not replay from there and should be kept
    as the comment text it is. All of it or none: a partial chain would be a line
    the author never wrote.
    """
    heads: list[MoveNode] = []
    line: list[_LineStep] = []
    board = anchor_board.copy()
    pending = ""  # prose waiting for the move it trails
    lead = ""  # prose before the block's first move, which trails nothing

    for item in block:
        if isinstance(item, _VarComment):
            pending = f"{pending}\n\n{item.text}" if pending else item.text
            continue

        if pending:
            if line:
                line[-1].node.post_comment = pending
            else:
                lead = pending
            pending = ""

        ply = _ply_number(item.raw)
        if ply is not None and ply != _board_ply(board):
            at = next(
                (i for i, step in enumerate(line) if _board_ply(step.board) == ply),
                None,
            )
            if at is None:
                return [], _block_text(block)
            board = line[at].board.copy()
            del line[at:]

        try:
            move = board.parse_san(item.san)
        except ValueError:
            return [], _block_text(block)
        node = MoveNode(san=item.san, nags=list(item.nags))
        if line:
            line[-1].node.children.append(node)
        else:
            node.pre_comment = lead
            lead = ""
            heads.append(node)
        line.append(_LineStep(board.copy(), node))
        board.push(move)

    if not heads:
        return [], _block_text(block)
    if pending:
        line[-1].node.post_comment = pending
    return heads, ""


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

    root = MoveNode(san=None)
    try:
        board = chess.Board(fen=starting_fen)
    except ValueError as exc:
        raise FetchError(f"chessable variation {oid} has an unreadable position: {exc}")

    parent_node = root
    parent_board = board.copy()
    continuations: list[MoveNode] = []

    for ply in plies:
        san = ply.get("san", "")
        if san == "--":
            continue
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            # Stopping here would leave a complete-looking short game the runner
            # writes and every later run skips as done.
            raise FetchError(
                f"chessable variation {oid} has an unplayable move {san!r}: {exc}"
            ) from None

        pre_text, pre_vars = _comment(ply.get("before"))
        post_text, post_vars = _comment(ply.get("after"))
        arrows, highlights = _parse_draws(ply.get("draws", []))

        board.push(move)
        node = MoveNode(
            san=san,
            pre_comment=pre_text,
            post_comment=post_text,
            nags=_glyph_nags(ply.get("ann1", "")),
            arrows=arrows,
            highlights=highlights,
        )
        parent_node.children.append(node)
        parent_node.children.extend(continuations)
        continuations = []

        for block in pre_vars + post_vars:
            if _first_ply(block) == _board_ply(board):
                heads, unplayable = _build_variation_lines(board, block)
                continuations.extend(heads)
            else:
                heads, unplayable = _build_variation_lines(parent_board, block)
                parent_node.children.extend(heads)
            if unplayable:
                node.post_comment = _join(node.post_comment, unplayable)

        parent_board = board.copy()
        parent_node = node

    parent_node.children.extend(continuations)

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
            Variation(oid=str(v["oid"]), name=str(v.get("name", "")))
            for v in ch.get("variations", [])
            if "oid" in v
        ]
        chapters.append(Chapter(title=str(ch.get("title", "")), variations=variations))

    return CourseOutline(
        name=str(course.get("name", "")),
        owned=bool(course.get("owned")),
        chapters=chapters,
    )
