"""Anchoring semantics through the MoveTrainer interface: decoded plies in,
move tree out. The judgement history is docs/open-issues.md 5.1 and 5.7."""

from __future__ import annotations

import chess
import pytest

from pgn_dl.core.models import Arrow, Highlight
from pgn_dl.movetrainer import (
    Block,
    BlockComment,
    BlockMove,
    DecodedPly,
    UnplayableMoveError,
    build_tree,
    strip_san,
)


def _mv(raw: str) -> BlockMove:
    """A block move as a source decodes one ``S`` entry."""
    san, nags = strip_san(raw)
    return BlockMove(san, nags, raw.strip())


def test_a_block_replacing_its_ply_branches_where_that_ply_did() -> None:
    # "1.d4" is the same half-move as the 1.e4 it hangs off, so it is an
    # alternative to it and starts from the position 1.e4 started from.
    root = build_tree(
        chess.Board(),
        [DecodedPly("e4", post_blocks=[[_mv("1.d4"), _mv("1...d5")]])],
    )
    assert [c.san for c in root.children] == ["e4", "d4"]
    assert [c.san for c in root.children[1].children] == ["d5"]


def test_a_block_continuing_its_ply_hangs_below_it_after_the_mainline() -> None:
    # "1...c5" is the half-move after the 1.e4 it hangs off, so it is a reply
    # to that move rather than a replacement for it, and belongs below it. It
    # must land after the ply that really followed: children[0] is the
    # mainline, and putting the sideline there would rewrite the game.
    root = build_tree(
        chess.Board(),
        [
            DecodedPly("e4", post_blocks=[[_mv("1...c5"), _mv("2.Nf3")]]),
            DecodedPly("e5"),
        ],
    )
    e4 = root.children[0]
    assert [c.san for c in e4.children] == ["e5", "c5"]
    assert [c.san for c in e4.children[1].children] == ["Nf3"]


def test_a_block_holding_two_lines_from_one_position_becomes_two_variations() -> None:
    # The sites' editors pack whatever the author typed into one block and
    # nothing in the encoding separates the lines. The move number does: one
    # that goes back to a position already passed starts a new line there.
    block: Block = [_mv("1...e5"), _mv("2.Nf3"), _mv("1...c5"), _mv("2.Nf3")]
    root = build_tree(chess.Board(), [DecodedPly("e4", post_blocks=[block])])
    e4 = root.children[0]
    assert [c.san for c in e4.children] == ["e5", "c5"]
    assert [c.san for c in e4.children[0].children] == ["Nf3"]
    assert [c.san for c in e4.children[1].children] == ["Nf3"]


def test_a_line_that_goes_back_only_part_way_branches_part_way_down() -> None:
    root = build_tree(
        chess.Board(),
        [DecodedPly("e4", post_blocks=[[_mv("1...e5"), _mv("2.Nf3"), _mv("2.Bc4")]])],
    )
    e5 = root.children[0].children[0]
    assert e5.san == "e5"
    assert [c.san for c in e5.children] == ["Nf3", "Bc4"]


def test_a_block_naming_a_position_that_is_neither_stays_prose() -> None:
    # Anchoring it anywhere would be a guess, and a wrong guess replays as
    # moves the author never wrote. The sentence the author composed is
    # strictly better.
    root = build_tree(
        chess.Board(),
        [DecodedPly("e4", post_blocks=[[_mv("5.Nf3"), _mv("5...Nc6")]])],
    )
    assert [c.san for c in root.children] == ["e4"]
    assert "5.Nf3" in root.children[0].post_comment


def test_a_block_with_no_move_numbers_at_all_replaces_its_ply() -> None:
    # Nothing says otherwise, and an alternative is what the encoding meant
    # before the sites started writing numbers into the SAN.
    root = build_tree(
        chess.Board(),
        [DecodedPly("e4", post_blocks=[[_mv("d4"), _mv("d5")]])],
    )
    assert [c.san for c in root.children] == ["e4", "d4"]


def test_a_block_that_cannot_be_replayed_is_kept_whole_as_text() -> None:
    # All of it or none: a partial chain would be a line the author never
    # wrote, and the block's moves and prose are one sentence upstream.
    block = [
        BlockComment("Better was"),
        _mv("1.d4"),
        _mv("1...Qz9"),
        BlockComment("with an edge."),
    ]
    root = build_tree(chess.Board(), [DecodedPly("e4", post_blocks=[block])])
    assert [c.san for c in root.children] == ["e4"]  # no half-parsed branch
    text = root.children[0].post_comment
    assert "Better was" in text
    assert "1.d4" in text
    assert "1...Qz9" in text
    assert "with an edge." in text


def test_unplayable_block_text_joins_an_existing_post_comment() -> None:
    root = build_tree(
        chess.Board(),
        [
            DecodedPly(
                "e4",
                post_comment="Already said.",
                post_blocks=[[_mv("5.Nf3")]],
            )
        ],
    )
    assert root.children[0].post_comment == "Already said.\n\n5.Nf3"


def test_a_comment_inside_a_block_attaches_to_the_move_it_follows() -> None:
    # A comment between two moves belongs to the move it follows, not to the
    # last move of the line.
    block = [_mv("1.d4"), BlockComment("comment for d4"), _mv("1...d5")]
    root = build_tree(chess.Board(), [DecodedPly("e4", post_blocks=[block])])
    d4 = root.children[1]
    assert d4.san == "d4"
    assert d4.post_comment == "comment for d4"
    assert d4.children[0].san == "d5"
    assert d4.children[0].post_comment == ""


def test_lead_prose_becomes_the_first_moves_pre_comment() -> None:
    root = build_tree(
        chess.Board(),
        [DecodedPly("e4", post_blocks=[[BlockComment("Better was"), _mv("1.d4")]])],
    )
    assert root.children[1].san == "d4"
    assert root.children[1].pre_comment == "Better was"


def test_trailing_prose_lands_on_the_last_move_of_the_line() -> None:
    block = [_mv("1.d4"), _mv("1...d5"), BlockComment("with an edge.")]
    root = build_tree(chess.Board(), [DecodedPly("e4", post_blocks=[block])])
    d5 = root.children[1].children[0]
    assert d5.san == "d5"
    assert d5.post_comment == "with an edge."


def test_the_last_plys_continuations_become_its_line_and_variations() -> None:
    # The last ply has no mainline move after it, so its continuations are all
    # it has: the first reads as the line's continuation and the rest as
    # variations of that, which is the only shape PGN can hold them in (the
    # 5.1 judgement).
    root = build_tree(
        chess.Board(),
        [DecodedPly("e4", post_blocks=[[_mv("1...c5"), _mv("2.Nf3"), _mv("1...e5")]])],
    )
    e4 = root.children[0]
    assert [c.san for c in e4.children] == ["c5", "e5"]
    assert [c.san for c in e4.children[0].children] == ["Nf3"]


def test_a_glyph_run_on_a_block_move_reaches_the_nodes_nags() -> None:
    root = build_tree(chess.Board(), [DecodedPly("e4", post_blocks=[[_mv("1.d4?!")]])])
    assert root.children[1].san == "d4"
    assert root.children[1].nags == [6]


def test_an_unplayable_mainline_move_raises_the_modules_error() -> None:
    # Stopping at the bad ply would keep a complete-looking short game that
    # the runner writes, counts, and skips on every later run.
    with pytest.raises(UnplayableMoveError) as exc:
        build_tree(
            chess.Board(),
            [DecodedPly("e4"), DecodedPly("e5"), DecodedPly("Nf9")],
        )
    assert exc.value.san == "Nf9"
    assert "Nf9" in str(exc.value)


def test_a_null_move_ply_is_skipped() -> None:
    root = build_tree(
        chess.Board(), [DecodedPly("e4"), DecodedPly("--"), DecodedPly("e5")]
    )
    assert root.children[0].san == "e4"
    assert root.children[0].children[0].san == "e5"


def test_per_ply_annotations_land_on_the_node() -> None:
    ply = DecodedPly(
        "e4",
        pre_comment="before.",
        post_comment="after.",
        nags=[1],
        arrows=[Arrow(from_square="e2", to_square="e4", type="green")],
        highlights=[Highlight(square="d5", type="red")],
    )
    node = build_tree(chess.Board(), [ply]).children[0]
    assert node.pre_comment == "before."
    assert node.post_comment == "after."
    assert node.nags == [1]
    assert [(a.from_square, a.to_square, a.type) for a in node.arrows] == [
        ("e2", "e4", "green")
    ]
    assert [(h.square, h.type) for h in node.highlights] == [("d5", "red")]


def test_build_tree_does_not_mutate_the_callers_board() -> None:
    board = chess.Board()
    build_tree(board, [DecodedPly("e4"), DecodedPly("e5")])
    assert board.move_stack == []
