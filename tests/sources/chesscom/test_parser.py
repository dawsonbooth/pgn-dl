"""Parser tests against captured chess.com API responses.

See ``tests/fixtures/chesscom/``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import chess
import chess.pgn
import pytest

from pgn_dl.core.models import Collection, GameItem
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.sources.chesscom.parser import (
    _build_variation_lines,
    _strip_html,
    _strip_san,
    _VarComment,
    _VarMove,
    parse_course_skeleton,
    parse_lesson_tree,
)

FIX = Path(__file__).parents[2] / "fixtures" / "chesscom"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def _lesson(plies: list[dict]) -> dict:
    """A minimal ``GetCourseTrainingForLearning`` response around ``plies``."""
    return {
        "lesson": {
            "variation": {
                "id": "v1",
                "name": "Test",
                "startingPosition": {"fen": chess.STARTING_FEN},
            },
            "plies": plies,
        }
    }


def _variation_comment(items: list[dict]) -> str:
    return json.dumps({"data": [{"key": "V", "val": items}]})


def test_course_skeleton_extracts_chapters_and_lessons() -> None:
    info, headers = parse_course_skeleton(_load("learning_session.json"))
    assert info.name == "Aman Hambleton's London System"
    assert info.slug == "aman-hambleton-s-london-system-353577"
    assert len(info.chapters) == 23
    assert info.chapters[0][1] == "Introduction"
    assert info.author == "Aman Hambleton"
    assert len(headers) == 866
    assert headers[0]["chapterId"] == info.chapters[0][0]


def test_lesson_intro_treats_null_move_as_description() -> None:
    lesson = parse_lesson_tree(_load("lesson_intro.json"))
    assert lesson.id == "60801362-0232-47cd-86e9-02b0d3c37a84"
    assert "Aman Hambleton" in lesson.description
    # No real moves in the intro.
    assert lesson.root.children == []


def test_lesson_real_game_parses_mainline_and_variations() -> None:
    lesson = parse_lesson_tree(_load("lesson_real_game.json"))
    # 36 plies announced; root.children[0] is move 1, then walk mainline.
    mainline = []
    node = lesson.root
    while node.children:
        node = node.children[0]
        mainline.append(node.san)
    assert mainline[:8] == ["d4", "d5", "Bf4", "Nf6", "e3", "e6", "Nd2", "c5"]
    # 36 full moves announced; this game ends with white's 36th, so 71 plies.
    # The last of them carries two replies to it that the author analysed but
    # nobody played, and PGN has no way to write an alternative to a move that
    # does not exist: the first of them continues the line, the second is a
    # variation of it. See test_the_closing_block_of_the_real_game_becomes_two_replies.
    assert mainline[:71][-1] == "Bxf6"
    assert mainline[71:] == ["Qf7", "Qh6+"]

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


# Neither committed lesson fixture carries a single arrow, highlight or
# position NAG, so the whole annotation branch is unobservable against them.
_ANNOTATED_POSITION = {
    "arrows": [
        {
            "squareFrom": "SQUARE_E2",
            "squareTo": "SQUARE_E4",
            "type": "COURSE_DRAWING_ARROW_TYPE_SECONDARY",
        },
        {
            "squareFrom": "SQUARE_G1",
            "squareTo": "SQUARE_F3",
            "type": "COURSE_DRAWING_ARROW_TYPE_TERTIARY",
        },
        # No type at all, and a type the map does not know: both are drawings
        # the author made, so neither may be dropped.
        {"squareFrom": "SQUARE_D1", "squareTo": "SQUARE_H5"},
        {"squareFrom": "SQUARE_A1", "squareTo": "SQUARE_A8", "type": "WHAT_IS_THIS"},
    ],
    "highlights": [
        {"square": "SQUARE_F7", "type": "COURSE_DRAWING_SQUARE_TYPE_TERTIARY"},
        {"square": "SQUARE_D5", "type": "COURSE_DRAWING_SQUARE_TYPE_SECONDARY"},
        {"square": "SQUARE_C3"},
    ],
    # A string id, an integer one, and an entry with none.
    "nags": [{"nagId": "5"}, {"nagId": 14}, {"comment": "no id here"}],
}


def test_position_annotations_reach_the_move_they_belong_to() -> None:
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "position": _ANNOTATED_POSITION}]))
    node = lesson.root.children[0]

    assert [(a.from_square, a.to_square, a.type) for a in node.arrows] == [
        ("e2", "e4", "secondary"),
        ("g1", "f3", "tertiary"),
        ("d1", "h5", "primary"),  # no type named
        ("a1", "a8", "primary"),  # a type this parser has never seen
    ]
    assert [(h.square, h.type) for h in node.highlights] == [
        ("f7", "tertiary"),
        ("d5", "secondary"),
        ("c3", "primary"),
    ]
    assert node.nags == [5, 14]  # the entry carrying no nagId is skipped


def test_position_annotations_survive_into_the_pgn() -> None:
    # The parser filling the fields is only half of it: they have to come out
    # as the %cal / %csl commands a GUI reads, or the drawings are lost anyway.
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "position": _ANNOTATED_POSITION}]))
    collection = Collection(source="chesscom", kind="courses", slug="c", name="C")
    pgn = game_to_pgn(collection, GameItem(root=lesson.root))

    assert "[%cal Ye2e4,Rg1f3,Gd1h5,Ga1a8]" in pgn
    assert "[%csl Rf7,Yd5,Gc3]" in pgn
    assert "$5" in pgn
    assert "$14" in pgn


def test_a_position_with_no_annotations_adds_nothing() -> None:
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "position": {"fen": "..."}}]))
    node = lesson.root.children[0]
    assert node.arrows == [] and node.highlights == [] and node.nags == []


def test_strip_san_maps_trailing_eval_glyphs_to_nags() -> None:
    assert _strip_san("9.Ne5?!") == ("Ne5", [6])  # move number + dubious glyph
    assert _strip_san("Qa5=") == ("Qa5", [10])  # equal position
    assert _strip_san("Nfe4∞") == ("Nfe4", [13])  # unclear position
    # A check marker is part of the move notation, not a NAG: leave it in place
    # so it is never silently deleted.
    assert _strip_san("Nf3+") == ("Nf3+", [])


def test_strip_san_records_every_glyph_it_strips() -> None:
    # A glyph run stripped off the SAN and mapped to nothing is an annotation
    # deleted from the lesson.
    assert _strip_san("24.Rbe1+-") == ("Rbe1", [18])  # in lesson_real_game.json
    assert _strip_san("Nf3-+") == ("Nf3", [19])
    assert _strip_san("Nf3+/-") == ("Nf3", [16])
    assert _strip_san("Nf3!?!") == ("Nf3", [5, 1])
    assert _strip_san("Nf3=∞") == ("Nf3", [10, 13])


def test_strip_html_keeps_the_text_after_the_last_tag_or_entity() -> None:
    # HTMLParser withholds a trailing entity until it knows the entity ended.
    assert _strip_html("White is better &") == "White is better &"
    assert _strip_html("<b>Careful</b>: the pawn falls") == "Careful: the pawn falls"


def test_strip_html_keeps_a_less_than_sign_that_opens_no_tag() -> None:
    # Comparisons are ordinary in chess prose, and chess.com's editor does not
    # escape them; HTMLParser reads one as an unterminated tag and eats the rest
    # of the comment looking for its ">".
    assert _strip_html("a<b in the pawn race, so White wins easily.") == (
        "a<b in the pawn race, so White wins easily."
    )
    assert _strip_html("5<6>4") == "5<6>4"
    assert _strip_html("<i>still</i> a tag") == "still a tag"


def test_strip_html_keeps_the_breaks_it_inserts() -> None:
    assert _strip_html("<p>One.</p><p>Two.</p>") == "One.\n\nTwo."
    assert _strip_html("a<br>b") == "a\nb"
    assert _strip_html("keeps   one   space") == "keeps one space"


def test_an_unplayable_mainline_move_raises_instead_of_truncating() -> None:
    # Stopping at the bad ply keeps a complete-looking short game: the runner
    # writes it, counts it, and every later run skips it.
    raw = _lesson([{"san": "e4"}, {"san": "e5"}, {"san": "Nf9"}, {"san": "Nc6"}])
    with pytest.raises(ValueError) as exc:
        parse_lesson_tree(raw)
    assert "Nf9" in str(exc.value)


def test_a_variation_replacing_its_ply_branches_where_that_ply_did() -> None:
    # "1.d4" is the same half-move as the 1.e4 it hangs off, so it is an
    # alternative to it and starts from the position 1.e4 started from.
    post = _variation_comment([{"key": "S", "val": "1.d4"}, {"key": "S", "val": "1...d5"}])
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    assert [c.san for c in lesson.root.children] == ["e4", "d4"]
    assert [c.san for c in lesson.root.children[1].children] == ["d5"]


def test_a_variation_continuing_its_ply_hangs_below_it_after_the_mainline() -> None:
    # "1...c5" is the half-move after the 1.e4 it hangs off, so it is a reply to
    # that move rather than a replacement for it, and belongs below it. It must
    # land after the ply that really followed: children[0] is the mainline, and
    # putting the sideline there would rewrite the game.
    post = _variation_comment([{"key": "S", "val": "1...c5"}, {"key": "S", "val": "2.Nf3"}])
    lesson = parse_lesson_tree(
        _lesson([{"san": "e4", "postComment": post}, {"san": "e5"}])
    )

    e4 = lesson.root.children[0]
    assert [c.san for c in e4.children] == ["e5", "c5"]
    assert [c.san for c in e4.children[1].children] == ["Nf3"]


def test_a_block_holding_two_lines_from_one_position_becomes_two_variations() -> None:
    # chess.com's editor packs whatever the author typed into one block and
    # nothing in the encoding separates the lines. The move number does: one
    # that goes back to a position already passed starts a new line there.
    post = _variation_comment(
        [
            {"key": "S", "val": "1...e5"},
            {"key": "S", "val": "2.Nf3"},
            {"key": "S", "val": "1...c5"},
            {"key": "S", "val": "2.Nf3"},
        ]
    )
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    e4 = lesson.root.children[0]
    assert [c.san for c in e4.children] == ["e5", "c5"]
    assert [c.san for c in e4.children[0].children] == ["Nf3"]
    assert [c.san for c in e4.children[1].children] == ["Nf3"]


def test_a_line_that_goes_back_only_part_way_branches_part_way_down() -> None:
    post = _variation_comment(
        [
            {"key": "S", "val": "1...e5"},
            {"key": "S", "val": "2.Nf3"},
            {"key": "S", "val": "2.Bc4"},
        ]
    )
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    e5 = lesson.root.children[0].children[0]
    assert e5.san == "e5"
    assert [c.san for c in e5.children] == ["Nf3", "Bc4"]


def test_a_block_naming_a_position_that_is_neither_stays_prose() -> None:
    # Anchoring it anywhere would be a guess, and a wrong guess replays as moves
    # the author never wrote. The sentence chess.com composed is strictly better.
    post = _variation_comment([{"key": "S", "val": "5.Nf3"}, {"key": "S", "val": "5...Nc6"}])
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    assert [c.san for c in lesson.root.children] == ["e4"]
    assert "5.Nf3" in lesson.root.children[0].post_comment


def test_a_variation_with_no_move_numbers_at_all_replaces_its_ply() -> None:
    # Nothing says otherwise, and an alternative is what the encoding meant
    # before chess.com started writing numbers into the SAN.
    post = _variation_comment([{"key": "S", "val": "d4"}, {"key": "S", "val": "d5"}])
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    assert [c.san for c in lesson.root.children] == ["e4", "d4"]


def test_a_variation_that_cannot_be_replayed_is_kept_as_text() -> None:
    # The block's moves and prose are one sentence upstream, so a block that
    # cannot be replayed still reads as the author wrote it.
    post = _variation_comment(
        [
            {"key": "C", "val": "Better was"},
            {"key": "S", "val": "1.d4"},
            {"key": "S", "val": "1...Qz9"},
            {"key": "C", "val": "with an edge."},
        ]
    )
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    assert [c.san for c in lesson.root.children] == ["e4"]  # no half-parsed branch
    text = lesson.root.children[0].post_comment
    assert "Better was" in text
    assert "1.d4" in text
    assert "1...Qz9" in text
    assert "with an edge." in text


def test_a_variation_entry_holding_only_a_glyph_annotates_the_move_before_it() -> None:
    # chess.com writes one entry per SAN, so an entry that strips to no move at
    # all held nothing but an evaluation glyph. It has no node of its own; with
    # no move to carry it, it is an annotation deleted from the lesson.
    post = _variation_comment([{"key": "S", "val": "1.d4"}, {"key": "S", "val": "?!"}])
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    sideline = lesson.root.children[1]
    assert sideline.san == "d4"
    assert sideline.nags == [6]


def test_a_glyph_with_no_move_before_it_survives_as_text() -> None:
    # Nothing to annotate, so the glyph stays in the block's prose rather than
    # being dropped on the floor.
    post = _variation_comment([{"key": "S", "val": "?!"}, {"key": "S", "val": "1.d4"}])
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))

    sideline = lesson.root.children[1]
    assert sideline.san == "d4"
    assert sideline.pre_comment == "?!"


def test_a_comment_only_variation_block_is_kept() -> None:
    post = _variation_comment([{"key": "C", "val": "The rook is trapped."}])
    lesson = parse_lesson_tree(_lesson([{"san": "e4", "postComment": post}]))
    assert "The rook is trapped." in lesson.root.children[0].post_comment


def test_the_real_game_keeps_both_of_its_variation_blocks() -> None:
    lesson = parse_lesson_tree(_load("lesson_real_game.json"))
    mainline = []
    node = lesson.root
    while node.children:
        node = node.children[0]
        mainline.append(node)

    # 20...Qxh4?? ... 24.Rbe1+-: an eight-move line whose last SAN carries a
    # glyph, so leaving "+-" on it costs the whole line.
    siblings = mainline[38].children
    assert siblings[0].san == "Rfc8"
    sideline = next(c for c in siblings[1:] if c.san == "Qxh4")
    sans = []
    node = sideline
    while node is not None:
        sans.append(node.san)
        node = node.children[0] if node.children else None
    assert sans == ["Qxh4", "Bg5", "Qg4", "f3", "Qg3", "Bf6", "Kh7", "Rbe1"]
    assert sideline.nags == [4]  # the "??" on Qxh4


def test_the_closing_block_of_the_real_game_becomes_two_replies() -> None:
    # 36.Bxf6 is the last move of the game; the block hanging off it holds two
    # replies to it, "36...Qf7" and "36...Qe1+", both numbered as Black's 36th.
    # It is the one block in the fixture that is a continuation rather than an
    # alternative, and the one that holds two lines from a single position.
    lesson = parse_lesson_tree(_load("lesson_real_game.json"))
    mainline = []
    node = lesson.root
    while node.children:
        node = node.children[0]
        mainline.append(node)
    bxf6 = mainline[70]  # the game's 71st and last real ply

    assert bxf6.san == "Bxf6"
    assert [c.san for c in bxf6.children] == ["Qf7", "Qe1+"]
    assert bxf6.children[0].pre_comment.startswith("Black resigned because")
    assert [c.san for c in bxf6.children[0].children] == ["Qh6+"]
    assert bxf6.children[0].children[0].post_comment.startswith("leads to checkmate")
    kh2 = bxf6.children[1].children[0]
    assert kh2.san == "Kh2"
    assert "impossible to prevent" in kh2.post_comment


def test_the_real_game_still_renders_as_a_pgn_a_reader_can_open() -> None:
    # Every anchored block is replayed by game_to_pgn against its own board, so
    # a block placed at the wrong position fails the whole item here.
    lesson = parse_lesson_tree(_load("lesson_real_game.json"))
    collection = Collection(source="chesscom", kind="courses", slug="c", name="C")
    pgn = game_to_pgn(collection, GameItem(root=lesson.root))

    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None
    assert game.errors == []


def test_variation_comment_attaches_to_the_move_it_follows() -> None:
    # A 'C' comment between two 'S' moves in a chess.com variation block belongs
    # to the move it follows, not to the last move of the line.
    block = [
        _VarMove("Nf3", []),
        _VarComment("comment for Nf3"),
        _VarMove("Nf6", []),
    ]
    heads, unplayable = _build_variation_lines(chess.Board(), block)
    assert unplayable == ""
    assert [h.san for h in heads] == ["Nf3"]
    head = heads[0]
    assert head.post_comment == "comment for Nf3"
    child = head.children[0]
    assert child.san == "Nf6"
    assert child.post_comment == ""
