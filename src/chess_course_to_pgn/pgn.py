"""Serialize the internal :mod:`models` tree to PGN files via python-chess.

Layout: ``<out>/<course-slug>/<NN-chapter-slug>/<NNN-lesson-slug>.pgn``.

Annotation extensions in comments (Lichess/ChessBase ``%cal`` / ``%csl``):
- Arrows  -> ``[%cal Gd2d4,Re1e4,...]``
- Squares -> ``[%csl Re4,Gf6,...]``
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import chess
import chess.pgn

from .models import Arrow, Chapter, Course, Highlight, Lesson, MoveNode


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 via a temp file + ``os.replace``.

    The destination therefore only ever appears complete, so an interrupted run
    can't leave a half-written PGN that the resume-by-existence check would skip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# chess.com's three drawing levels map to Lichess/ChessBase colours.
_DRAWING_COLOR = {"primary": "G", "secondary": "Y", "tertiary": "R"}

_SLUG_DROP = re.compile(r"[^a-zA-Z0-9]+")


def slugify(s: str, fallback: str = "untitled") -> str:
    out = _SLUG_DROP.sub("-", s).strip("-").lower()
    return out or fallback


def course_dir(out: Path, course: Course) -> Path:
    """Root directory for a course's PGNs: ``<out>/<course-slug>``."""
    return out / slugify(course.slug or course.name)


def lesson_path(
    out: Path, course: Course, chapter: Chapter, idx: int, name: str
) -> Path:
    """Path for one lesson: ``<course-dir>/<NN-chapter>/<NNN-lesson>.pgn``.

    This numbering is the resume contract (the scraper skips a lesson whose file
    already exists), so it lives in exactly one place.
    """
    return (
        course_dir(out, course)
        / f"{chapter.idx:02d}-{slugify(chapter.name, 'chapter')}"
        / f"{idx:03d}-{slugify(name, 'lesson')}.pgn"
    )


def _build_move_comment(node: MoveNode) -> str:
    parts: list[str] = []
    if node.post_comment:
        parts.append(node.post_comment)
    if node.arrows:
        parts.append(_arrows_pgn(node.arrows))
    if node.highlights:
        parts.append(_highlights_pgn(node.highlights))
    return " ".join(p for p in parts if p)


def _arrows_pgn(arrows: list[Arrow]) -> str:
    items = ",".join(
        f"{_DRAWING_COLOR.get(a.type, 'G')}{a.from_square}{a.to_square}" for a in arrows
    )
    return f"[%cal {items}]"


def _highlights_pgn(highlights: list[Highlight]) -> str:
    items = ",".join(f"{_DRAWING_COLOR.get(h.type, 'G')}{h.square}" for h in highlights)
    return f"[%csl {items}]"


def _attach_children(
    pgn_parent: chess.pgn.GameNode,
    parent_board: chess.Board,
    children: list[MoveNode],
) -> None:
    for child in children:
        if child.san is None:
            continue
        try:
            move = parent_board.parse_san(child.san)
        except chess.IllegalMoveError, chess.AmbiguousMoveError, ValueError:
            continue

        # First child is the mainline continuation; siblings are variations.
        pgn_child = pgn_parent.add_variation(move)
        if child.pre_comment:
            pgn_child.starting_comment = child.pre_comment
        comment = _build_move_comment(child)
        if comment:
            pgn_child.comment = comment
        for nag in child.nags:
            pgn_child.nags.add(nag)

        next_board = parent_board.copy()
        next_board.push(move)
        _attach_children(pgn_child, next_board, child.children)


def lesson_to_pgn(course: Course, chapter: Chapter, lesson: Lesson) -> str:
    """Return the PGN text for one lesson."""
    game = chess.pgn.Game()
    game.headers["Event"] = course.name
    game.headers["Site"] = "chess.com"
    game.headers["Round"] = f"{chapter.idx}.{lesson.idx}"
    game.headers["White"] = "?"
    game.headers["Black"] = "?"
    game.headers["Date"] = "????.??.??"
    game.headers["Result"] = "*"
    if course.authors:
        game.headers["Annotator"] = course.authors[0].name
    game.headers["ChessComLessonId"] = lesson.id

    if lesson.starting_fen and lesson.starting_fen != chess.STARTING_FEN:
        game.headers["FEN"] = lesson.starting_fen
        game.headers["SetUp"] = "1"

    if lesson.description:
        game.comment = lesson.description

    board = chess.Board(fen=lesson.starting_fen)
    _attach_children(game, board, lesson.root.children)

    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
    return game.accept(exporter)


_COMBINED_NAME = "all.pgn"


def _course_order_key(path: Path) -> tuple[int, int]:
    """Sort key that orders ``<NN-chapter>/<NNN-lesson>.pgn`` by chapter then
    lesson number, independent of zero-pad width (so chapter 100 follows 99)."""

    def leading_int(name: str) -> int:
        m = re.match(r"\d+", name)
        return int(m.group()) if m else 0

    return leading_int(path.parent.name), leading_int(path.name)


def combine_course_dir(course_dir: Path) -> Path:
    """Concatenate every per-lesson PGN under ``course_dir`` into ``<course_dir>/all.pgn``.

    Only PGNs in ``course_dir/<chapter>/<lesson>.pgn`` are included; files at
    the top level (such as the combined file itself or hand-rolled exports) are
    skipped. Files are ordered by their numeric ``NN-`` / ``NNN-`` prefixes so
    the combined file follows course order.
    """
    out_file = course_dir / _COMBINED_NAME
    files = sorted(
        (f for f in course_dir.glob("*/*.pgn") if f.is_file()),
        key=_course_order_key,
    )
    chunks: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8").rstrip()
        if text:
            chunks.append(text)
    write_text_atomic(out_file, "\n\n".join(chunks) + "\n")
    return out_file
