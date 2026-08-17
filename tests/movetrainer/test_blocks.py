"""The annotation-block item model and block builder."""

from __future__ import annotations

from pgn_dl.movetrainer import BlockComment, BlockMove, parse_annotation_data


def _entries(vals: list[str]) -> list[dict]:
    """One ``V`` entry holding one ``S`` entry per SAN, as both sites write."""
    return [{"key": "V", "val": [{"key": "S", "val": v} for v in vals]}]


def test_prose_concatenates_and_blocks_split_out() -> None:
    text, blocks = parse_annotation_data(
        [
            {"key": "C", "val": "<p>One.</p>"},
            {"key": "C", "val": "Two."},
            {
                "key": "V",
                "val": [{"key": "S", "val": "1.d4"}, {"key": "C", "val": "wins."}],
            },
        ]
    )
    assert text == "One.\n\nTwo."
    assert blocks == [[BlockMove("d4", [], "1.d4"), BlockComment("wins.")]]


def test_a_block_entry_holding_only_a_glyph_annotates_the_move_before_it() -> None:
    # The sites write one entry per SAN, so an entry that strips to no move at
    # all held nothing but an evaluation glyph. It has no node of its own; with
    # no move to carry it, it is an annotation deleted from the lesson.
    _, blocks = parse_annotation_data(_entries(["1.d4", "?!"]))
    assert blocks == [[BlockMove("d4", [6], "1.d4")]]


def test_a_glyph_with_no_move_before_it_survives_as_text() -> None:
    # Nothing to annotate, so the glyph stays in the block's prose rather than
    # being dropped on the floor.
    _, blocks = parse_annotation_data(_entries(["?!", "1.d4"]))
    assert blocks == [[BlockComment("?!"), BlockMove("d4", [], "1.d4")]]


def test_a_custom_cleaner_reaches_prose_and_trailing_comments() -> None:
    # A source with markup of its own (Chessable's bracket markers) wraps
    # ``strip_html`` and passes the wrapper here.
    text, blocks = parse_annotation_data(
        [
            {"key": "C", "val": "lead"},
            {
                "key": "V",
                "val": [{"key": "S", "val": "1.e4"}, {"key": "C", "val": "trail"}],
            },
        ],
        clean_text=str.upper,
    )
    assert text == "LEAD"
    assert blocks == [[BlockMove("e4", [], "1.e4"), BlockComment("TRAIL")]]


def test_entries_that_decode_to_nothing_are_dropped() -> None:
    text, blocks = parse_annotation_data(
        [
            {"key": "C", "val": "<p></p>"},  # strips to nothing
            {"key": "V", "val": []},  # an empty block is no block
            {"key": "X", "val": "unknown"},
        ]
    )
    assert text == ""
    assert blocks == []
