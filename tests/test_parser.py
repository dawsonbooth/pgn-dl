"""Parser tests against captured chess.com API responses (see tests/fixtures/)."""

from __future__ import annotations

import json
from pathlib import Path

import chess

from chess_course_to_pgn.parser import (
    _build_variation_branch,
    _strip_san,
    _VarComment,
    _VarMove,
    parse_course_skeleton,
    parse_lesson,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_course_skeleton_extracts_chapters_and_lessons() -> None:
    course, headers = parse_course_skeleton(_load("learning_session.json"))
    assert course.id == "6445752b-f404-4d8f-9397-4fb03f690d8e"
    assert course.name == "Aman Hambleton's London System"
    assert course.slug == "aman-hambleton-s-london-system-353577"
    assert len(course.chapters) == 23
    assert course.chapters[0].name == "Introduction"
    assert course.authors[0].name == "Aman Hambleton"
    assert course.authors[0].chess_title == "gm"
    assert len(headers) == 866
    assert headers[0]["chapterId"] == course.chapters[0].id


def test_lesson_intro_treats_null_move_as_description() -> None:
    lesson = parse_lesson(_load("lesson_intro.json"), idx=1)
    assert lesson.name == "Welcome to the London!"
    assert "Aman Hambleton" in lesson.description
    # No real moves in the intro.
    assert lesson.root.children == []


def test_lesson_real_game_parses_mainline_and_variations() -> None:
    lesson = parse_lesson(_load("lesson_real_game.json"), idx=3)
    # 36 plies announced; root.children[0] is move 1, then walk mainline.
    mainline = []
    node = lesson.root
    while node.children:
        node = node.children[0]
        mainline.append(node.san)
    assert mainline[:8] == ["d4", "d5", "Bf4", "Nf6", "e3", "e6", "Nd2", "c5"]
    # 36 full moves announced; this game ends with white's 36th, so 71 plies.
    assert len(mainline) == 71

    # Move 9 (e4) has a postComment variation starting with Ne5?!.
    # Find the e4 node and check it has a sibling for the variation.
    node = lesson.root
    for _ in range(16):  # 8 full moves = 16 plies; e4 is the 17th
        node = node.children[0]
    e4 = node.children[0]
    assert e4.san == "e4"
    # The parent of e4 (8...b6) should have e4 plus at least one variation child.
    assert len(node.children) >= 2
    variation_head = node.children[1]
    assert variation_head.san == "Ne5"


def test_strip_san_maps_trailing_eval_glyphs_to_nags() -> None:
    assert _strip_san("9.Ne5?!") == ("Ne5", [6])  # move number + dubious glyph
    assert _strip_san("Qa5=") == ("Qa5", [10])  # equal position
    assert _strip_san("Nfe4∞") == ("Nfe4", [13])  # unclear position
    # A check marker is part of the move notation, not a NAG: leave it in place
    # so it is never silently deleted.
    assert _strip_san("Nf3+") == ("Nf3+", [])


def test_variation_comment_attaches_to_the_move_it_follows() -> None:
    # A 'C' comment between two 'S' moves in a chess.com variation block belongs
    # to the move it follows, not to the last move of the line.
    block = [
        _VarMove("Nf3", []),
        _VarComment("comment for Nf3"),
        _VarMove("Nf6", []),
    ]
    head = _build_variation_branch(chess.Board(), block)
    assert head is not None
    assert head.san == "Nf3"
    assert head.post_comment == "comment for Nf3"
    child = head.children[0]
    assert child.san == "Nf6"
    assert child.post_comment == ""
