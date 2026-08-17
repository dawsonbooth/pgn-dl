"""The NAG glyph vocabulary, through the MoveTrainer interface."""

from __future__ import annotations

from pgn_dl.movetrainer import glyph_nags, strip_san


def test_strip_san_maps_trailing_eval_glyphs_to_nags() -> None:
    assert strip_san("9.Ne5?!") == ("Ne5", [6])  # move number + dubious glyph
    assert strip_san("Qa5=") == ("Qa5", [10])  # equal position
    assert strip_san("Nfe4∞") == ("Nfe4", [13])  # unclear position
    # A check marker is part of the move notation, not a NAG: leave it in place
    # so it is never silently deleted.
    assert strip_san("Nf3+") == ("Nf3+", [])


def test_strip_san_records_every_glyph_it_strips() -> None:
    # A glyph run stripped off the SAN and mapped to nothing is an annotation
    # deleted from the lesson.
    assert strip_san("24.Rbe1+-") == ("Rbe1", [18])  # in lesson_real_game.json
    assert strip_san("Nf3-+") == ("Nf3", [19])
    assert strip_san("Nf3+/-") == ("Nf3", [16])
    assert strip_san("Nf3!?!") == ("Nf3", [5, 1])
    assert strip_san("Nf3=∞") == ("Nf3", [10, 13])


def test_glyph_nags_reads_a_whole_run() -> None:
    # Chessable's ``ann1`` field is read through this: a glyph run, no SAN.
    assert glyph_nags("?") == [2]
    assert glyph_nags("±") == [16]
    assert glyph_nags("=∞") == [10, 13]
    assert glyph_nags(" !? ") == [5]


def test_glyph_nags_yields_nothing_for_anything_not_wholly_glyphs() -> None:
    assert glyph_nags("") == []
    assert glyph_nags("good move") == []
    assert glyph_nags("?x") == []
