"""chessreps opening courses.

A course is a list of named SAN lines sharing prefixes, plus two FEN-keyed
maps: the author's commentary (keyed by full 6-field FEN) and engine evals
(keyed by the 4-field prefix). Merging the lines into a trie in listing order
rebuilds the repertoire: the first line to reach a position owns the mainline
slot, later lines become variations, which is the site's own ordering.

Comments and evals ride onto nodes during the replay that validates each move,
so the finished tree renders through ``core.pgn`` with nothing chessreps-shaped
left in it. Evals become ``[%eval ...]`` comment commands, which Lichess and
most GUIs read.
"""

from __future__ import annotations

import re

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef, MoveNode
from ...core.paths import COMBINED_NAME, slugify
from . import auth
from .parser import Course, ParsedPage, parse_payload

#: Where a downloaded game came from, for the PGN ``Site`` header.
SITE = "chessreps.com"

#: A move-number token in a line ("1.", "12.", also "1..." after a branch).
_MOVENUM = re.compile(r"\d+\.(\.\.)?")

#: A course URL (optionally localized, e.g. /es/opening/...), or a bare slug.
#: Strict end anchoring for the reason noctie's is: the site serves HTTP 200
#: for every path, so a mistyped target must fail here or nowhere.
_TARGET_RE = re.compile(
    r"^(?:https?://(?:www\.)?chessreps\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?opening/)?"
    r"(?P<slug>[a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)


def slug_from(target: str) -> str:
    """The course slug in a chessreps opening URL, or a bare slug."""
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET_RE.match(cleaned)
    if m is None:
        raise ValueError(f"could not read a chessreps opening slug from {target!r}")
    return m.group("slug").lower()


def _tokens(line: str) -> list[str]:
    return [t for t in line.split() if not _MOVENUM.fullmatch(t)]


def _format_eval(entry: object) -> str:
    """One eval as an ``[%eval ...]`` command, or "" for a shape unknown here.

    Scores are centipawns from White's point of view (verified against the live
    site from both sides to move); ``[%eval]`` wants pawns. An entry this
    misses costs one annotation, not the item, so unknowns are skipped.
    """
    if not isinstance(entry, dict):
        return ""
    score = entry.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float):
        return ""
    if entry.get("type") == "score":
        return f"[%eval {score / 100:.2f}]"
    if entry.get("type") == "mate":
        return f"[%eval #{int(score)}]"
    return ""


def _annotations(board: chess.Board, course: Course) -> str:
    """What the course says about the position ``board`` has reached."""
    full = board.fen()
    prefix = " ".join(full.split()[:4])
    parts = [course.descriptions.get(full, ""), _format_eval(course.evals.get(prefix))]
    return " ".join(p for p in parts if p)


def _replay(course: Course, number: int, line: str) -> list[tuple[str, chess.Board]]:
    """One line's moves as (canonical SAN, board after the move) pairs.

    SAN is canonicalized through python-chess so the same move spelled two ways
    in two lines ("Nxf3+" and "Nxf3") still merges to one node.
    """
    board = chess.Board()
    steps: list[tuple[str, chess.Board]] = []
    for san in _tokens(line):
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            raise FetchError(
                f"chessreps line {number} has an unplayable move {san!r}: {exc}"
            ) from None
        canonical = board.san(move)
        board.push(move)
        steps.append((canonical, board.copy(stack=False)))
    if not steps:
        # A line of only move-number tokens would render as a headers-only
        # game the runner writes and every later run skips as done.
        raise FetchError(f"chessreps line {number} has no moves")
    return steps


def build_tree(course: Course) -> MoveNode:
    """The whole repertoire as one merged trie, line names tagged on leaves."""
    root = MoveNode(san=None)
    for number, line in enumerate(course.lines, start=1):
        node = root
        for canonical, board in _replay(course, number, line):
            child = next((c for c in node.children if c.san == canonical), None)
            if child is None:
                child = MoveNode(
                    san=canonical, post_comment=_annotations(board, course)
                )
                node.children.append(child)
            node = child
        name = course.line_names.get(line)
        if name and (tag := f"(line: {name})") not in node.post_comment:
            node.post_comment = f"{node.post_comment} {tag}".strip()
    return root


def build_line(course: Course, number: int) -> MoveNode:
    """One line as its own chain of moves, 1-based, carrying its annotations."""
    root = MoveNode(san=None)
    node = root
    for canonical, board in _replay(course, number, course.lines[number - 1]):
        child = MoveNode(san=canonical, post_comment=_annotations(board, course))
        node.children.append(child)
        node = child
    return root


#: Prefix of a line item's ``ItemRef.id``, followed by the line's 1-based
#: position. The merged item carries the course id itself, so ``fetch`` can
#: tell the kinds apart by the prefix alone.
LINE_ID = "line:"

# Which PGN header names the side the repertoire is played from.
_REPERTOIRE_SIDE = {"w": "White", "b": "Black"}


def _merged_file_slug(name: str) -> str:
    """The merged game's filename, kept clear of the combined file.

    ``combine_collection_dir`` writes every other PGN into ``all.pgn``, so a
    course named "All" would otherwise have its tree overwritten by the
    concatenation. Same guard as noctie's book file.
    """
    slug = slugify(name)
    return f"{slug}-repertoire" if f"{slug}.pgn" == COMBINED_NAME else slug


class OpeningsExtractor(Extractor):
    """One chessreps opening course: the merged repertoire, plus its lines."""

    key = "chessreps:openings"
    url_patterns = (
        re.compile(
            r"^https?://(www\.)?chessreps\.com/([a-z]{2}(-[a-z]{2})?/)?opening/",
            re.IGNORECASE,
        ),
    )

    def __init__(self, target: str) -> None:
        self.slug = slug_from(target)
        self._page: ParsedPage | None = None

    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        cookie = await auth.session_cookie()
        url = f"https://www.chessreps.com/opening/{self.slug}/__data.json"
        text = await fetcher.get_text(url, headers={"Cookie": cookie})
        page = parse_payload(text)
        if not page.signed_in:
            # The route answers 200 either way; what an unaccepted session
            # changes is the content. Failing here beats silently writing the
            # anonymous trial subset. The runner appends the login hint.
            raise FetchError(
                "chessreps answered as signed out rather than as your account; "
                "the stored session was not accepted"
            )
        self._page = page
        course = page.course
        collection = self.collection(
            # Trial content is real content the account holds, but it must not
            # occupy the resume path of a future full-access download. A real
            # course slug already ending in -trial could collide here;
            # accepted as vanishingly unlikely.
            slug=self.slug + ("-trial" if course.trial else ""),
            name=course.name,
            headers={"Event": course.name, "Site": SITE},
        )
        items = [ItemRef(id=course.id, path=(_merged_file_slug(course.name),))]
        items += [
            ItemRef(
                id=f"{LINE_ID}{n}",
                path=(
                    "lines",
                    f"{n:03d}-"
                    + slugify(course.line_names.get(line) or "", fallback="line"),
                ),
            )
            for n, line in enumerate(course.lines, start=1)
        ]
        return collection, items

    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        if self._page is None:
            raise FetchError("chessreps fetch called before discover cached the course")
        course = self._page.course
        headers = {"ChessrepsOpeningId": course.id}
        side = _REPERTOIRE_SIDE[course.player_side]
        headers |= {"White": "?", "Black": "?", side: course.name}

        if ref.id.startswith(LINE_ID):
            number = int(ref.id.removeprefix(LINE_ID))
            line = course.lines[number - 1]
            name = course.line_names.get(line) or f"Line {number}"
            # Readers key on Event, so that names the line; the course keeps
            # its own header so the file still says where it came from.
            headers |= {"Event": name, "ChessrepsOpening": course.name}
            return GameItem(headers=headers, root=build_line(course, number))

        return GameItem(
            headers=headers, comment=course.description, root=build_tree(course)
        )
