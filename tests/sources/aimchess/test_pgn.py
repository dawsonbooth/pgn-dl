"""Comment text and PGN rewriting."""

from __future__ import annotations

import io

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.aimchess.pgn import comment_text, rewrite

from .conftest import EXERCISE, TUTORIAL


def test_paragraphs_are_separated_by_a_blank_line() -> None:
    assert comment_text("<p>One.</p>\n\n<p>Two.</p>") == "One.\n\nTwo."


def test_source_line_breaks_collapse_to_spaces() -> None:
    # ChessBase wraps lines at 80 columns, in HTML and plain comments alike.
    assert (
        comment_text("<p>The rook cuts\nthe king off.</p>")
        == "The rook cuts the king off."
    )
    assert comment_text("Exchange pawns,\nnot pieces.") == "Exchange pawns, not pieces."


def test_br_is_a_line_break() -> None:
    assert comment_text("<p>First<br />\nsecond</p>") == "First\nsecond"
    assert comment_text("<p>First<br />\n<br />\nsecond</p>") == "First\n\nsecond"


def test_ordered_list_items_are_numbered() -> None:
    raw = (
        "<p>Good when:</p>\n\n<ol>\n    <li>the position is open</li>\n"
        "    <li>the knight is restricted</li>\n</ol>\n\n<p>So it goes.</p>"
    )
    assert comment_text(raw) == (
        "Good when:\n\n1. the position is open\n2. the knight is restricted\n\nSo it goes."
    )


def test_entities_and_inline_tags() -> None:
    raw = "<p>Black&#39;s&nbsp;<strong>king</strong> &ndash; &quot;active&quot;</p>"
    assert comment_text(raw) == 'Black\'s king \u2013 "active"'


def test_links_keep_their_address() -> None:
    raw = '<p>We met <a href="/explore/4">connected</a> pawns.</p>'
    assert (
        comment_text(raw) == "We met connected (https://aimchess.com/explore/4) pawns."
    )


def test_the_end_marker_is_dropped() -> None:
    assert comment_text("%end% <p>Well done!</p>") == "Well done!"


def test_commands_go_in_front() -> None:
    assert (
        comment_text("[%cal Gf6e4] %end% <p>Well done!</p>")
        == "[%cal Gf6e4] Well done!"
    )
    assert comment_text("[%evp 0,4,-10]") == "[%evp 0,4,-10]"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[%cal\nGg5g6,Gg6g7]", "[%cal Gg5g6,Gg6g7]"),
        ("[%cal Gf3f2,\nGf2e1]", "[%cal Gf3f2,Gf2e1]"),
        ("[%evp 0,18,-725,\n-735]", "[%evp 0,18,-725,-735]"),
    ],
)
def test_commands_wrapped_by_chessbase_are_rejoined(raw: str, expected: str) -> None:
    assert comment_text(raw) == expected


def _render(pgn: str, **kwargs) -> chess.pgn.Game:
    text = rewrite(
        pgn, {"Event": "Lesson", "Site": "aimchess.com"}, subject="step", **kwargs
    )
    game = chess.pgn.read_game(io.StringIO(text))
    assert game is not None and not game.errors
    return game


def test_rewrite_replaces_event_and_site_and_keeps_the_rest() -> None:
    game = _render(TUTORIAL)
    assert (game.headers["Event"], game.headers["Site"]) == ("Lesson", "aimchess.com")
    assert game.headers["SourceVersionDate"] == "2024.01.01"
    assert game.headers["Black"] == "Tutorial"  # no player given: left alone


def test_rewrite_turns_every_comment_into_text() -> None:
    game = _render(EXERCISE)
    nf6 = game.next()
    assert nf6 is not None
    assert nf6.comment == "[%cal Gh5f7,Gc4f7] Black ignores the threat."
    assert len(nf6.arrows()) == 2
    mate, alternative = nf6.variations
    assert mate.comment == "Well done! That is mate."
    assert alternative.starting_comment == "Instead:"
    assert [m.san() for m in game.mainline()] == ["Nf6", "Qxf7#"]


def test_rewrite_names_the_player_on_the_chosen_side() -> None:
    # White is to move in the tutorial.
    replying = _render(TUTORIAL, player="Lesson")
    assert (replying.headers["White"], replying.headers["Black"]) == ("?", "Lesson")
    moving = _render(TUTORIAL, player="Lesson", player_moves_first=True)
    assert (moving.headers["White"], moving.headers["Black"]) == ("Lesson", "?")


def test_rewrite_puts_the_preface_first() -> None:
    game = _render(TUTORIAL, preface="A definition.")
    assert game.comment == "A definition.\n\n[%evp 0,2,900,910,920]"


def test_a_leading_byte_order_mark_is_harmless() -> None:
    game = _render("\ufeff" + TUTORIAL)
    assert game.headers["FEN"] == "8/8/8/8/8/1k6/8/1K5R w - - 0 1"


def test_an_unplayable_move_fails_the_game() -> None:
    with pytest.raises(FetchError, match="unreadable move"):
        rewrite("1. e4 e5 2. Ke3 *", {}, subject="step 3")
