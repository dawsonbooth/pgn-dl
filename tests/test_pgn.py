"""Round-trip a real lesson through parse -> PGN -> re-parse via python-chess."""

from __future__ import annotations

import io
import json
from pathlib import Path

import chess.pgn

from chess_course_to_pgn.models import Author, Chapter, Course
from chess_course_to_pgn.parser import parse_lesson
from chess_course_to_pgn.pgn import combine_course_dir, lesson_to_pgn

FIX = Path(__file__).parent / "fixtures"


def _make_course() -> tuple[Course, Chapter]:
    course = Course(
        id="c",
        slug="test-course",
        name="Test Course",
        authors=[Author(name="GM Tester", chess_title="gm")],
        chapters=[],
    )
    chapter = Chapter(id="ch", idx=1, name="Intro")
    course.chapters = [chapter]
    return course, chapter


def test_intro_lesson_emits_pgn_with_description_and_no_moves() -> None:
    lesson = parse_lesson(json.loads((FIX / "lesson_intro.json").read_text()), idx=1)
    course, chapter = _make_course()
    chapter.lessons = [lesson]
    pgn = lesson_to_pgn(course, chapter, lesson)

    assert '[Event "Test Course"]' in pgn
    assert '[Round "1.1"]' in pgn
    assert "Aman Hambleton" in pgn  # description body
    assert pgn.rstrip().endswith("*")  # no moves, just the result marker


def test_real_game_round_trips_via_python_chess() -> None:
    lesson = parse_lesson(
        json.loads((FIX / "lesson_real_game.json").read_text()), idx=3
    )
    course, chapter = _make_course()
    chapter.lessons = [lesson]
    pgn_text = lesson_to_pgn(course, chapter, lesson)

    reparsed = chess.pgn.read_game(io.StringIO(pgn_text))
    assert reparsed is not None
    assert reparsed.headers["Event"] == "Test Course"

    # Walk reparsed mainline; the first few should match the lesson.
    mainline = list(reparsed.mainline_moves())
    board = reparsed.board()
    sans = []
    for m in mainline[:8]:
        sans.append(board.san(m))
        board.push(m)
    assert sans == ["d4", "d5", "Bf4", "Nf6", "e3", "e6", "Nd2", "c5"]

    # At move 9 the mainline is e4; the lesson annotates an Ne5 alternative,
    # which must survive as a sibling variation through export and re-parse.
    node = reparsed
    for _ in range(16):  # 8 full moves = 16 plies -> the 8...b6 node
        node = node.variations[0]
    child_sans = [v.san() for v in node.variations]
    assert child_sans[0] == "e4"  # mainline continuation stays first
    assert "Ne5" in child_sans[1:]  # the annotated alternative is a sibling


def test_combine_orders_by_number_not_lexically(tmp_path: Path) -> None:
    # Chapter/lesson dirs are zero-padded, but once a number overflows the pad
    # width a plain string sort misorders it (100 < 99). combine_course_dir must
    # follow numeric course order regardless.
    course_dir = tmp_path / "course"
    for chapter, marker in [
        ("09-a", "AAA"),
        ("10-b", "BBB"),
        ("99-y", "YYY"),
        ("100-z", "ZZZ"),
    ]:
        lesson = course_dir / chapter / "001-l.pgn"
        lesson.parent.mkdir(parents=True)
        lesson.write_text(f'[Event "{marker}"]\n\n*\n', encoding="utf-8")

    combined = combine_course_dir(course_dir).read_text(encoding="utf-8")
    positions = [combined.index(m) for m in ("AAA", "BBB", "YYY", "ZZZ")]
    assert positions == sorted(positions)  # 09, 10, 99, 100 in numeric order
