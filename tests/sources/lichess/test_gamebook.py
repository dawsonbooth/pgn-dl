"""A gamebook's hints and replies to wrong moves, added to its export.

The chapter and its move tree are invented in the shapes Lichess sends (the
export as ``PgnDump`` writes it, glyphs on the move and variations after the
right move's comments; ``treeParts`` as the chapter's JSON carries it). None
of the text is Lichess's.
"""

from __future__ import annotations

from typing import Any

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess.comments import Note, annotate
from pgn_dl.sources.lichess.gamebook import DEVIATION, HINT, is_gamebook, notes

from .conftest import SACRIFICE, chapter

SUBJECT = "lichess study h7Sac1fz chapter Gb0ok123"

MOVES = (
    "{ Find White's moves. } 1. e4 { The center first. } 1... e5 2. Nf3!? "
    "{ Develops with a threat. } { [%cal Gf3e5] } "
    "(2. Bc4 { Also develops, but threatens nothing. }) "
    "(2. Qh5?! { Too early for the queen. } 2... Nc6) 2... Nc6 *"
)
GAMEBOOK = chapter("Gb0ok123", "Develop with tempo", MOVES, ChapterMode="gamebook")


def node(ply: int, san: str | None = None, **more: Any) -> dict[str, Any]:
    """A tree node as the JSON sends one; the moves' details are left out."""
    found: dict[str, Any] = {"ply": ply, "fen": "8/8/8/8/8/8/8/8 w - - 0 1"}
    if san is not None:
        found |= {"id": "??", "uci": "a1a1", "san": san}
    return found | more


def gamebook(hint: str | None = None, deviation: str | None = None) -> dict:
    found = {}
    if hint is not None:
        found["hint"] = hint
    if deviation is not None:
        found["deviation"] = deviation
    return {"gamebook": found}


def parts() -> list[dict[str, Any]]:
    """The tree of ``MOVES``: the mainline flat, each entry's ``children`` the
    variations to the next entry."""
    bc4 = node(3, "Bc4", comments=[{"id": "c1", "text": "Also develops"}])
    qh5 = node(
        3,
        "Qh5",
        glyphs=[{"id": 6, "symbol": "?!", "name": "Dubious move"}],
        children=[node(4, "Nc6", **gamebook(hint="The queen can be hit."))],
    )
    return [
        node(0, **gamebook(hint="Start in the center.")),
        node(1, "e4", **gamebook(deviation="Claim the center with a pawn.")),
        node(2, "e5", children=[bc4, qh5], **gamebook(hint="What attacks e5?")),
        node(
            3,
            "Nf3",
            glyphs=[{"id": 5, "symbol": "!?", "name": "Interesting move"}],
            **gamebook(deviation="Develop, and attack something {while} you do."),
        ),
        node(4, "Nc6"),
    ]


def test_a_chapter_says_whether_it_is_a_gamebook() -> None:
    assert is_gamebook(GAMEBOOK)
    assert not is_gamebook(SACRIFICE)


def test_every_hint_and_reply_is_found_where_the_tree_has_it() -> None:
    found = notes(parts(), SUBJECT)
    assert sorted(found, key=lambda n: n.path) == [
        Note((), (), f"{HINT}Start in the center."),
        Note((0,), ("e4",), f"{DEVIATION}Claim the center with a pawn."),
        Note((0, 0), ("e4", "e5"), f"{HINT}What attacks e5?"),
        Note(
            (0, 0, 0),
            ("e4", "e5", "Nf3"),
            f"{DEVIATION}Develop, and attack something {{while}} you do.",
        ),
        Note((0, 0, 2, 0), ("e4", "e5", "Qh5", "Nc6"), f"{HINT}The queen can be hit."),
    ]


def test_the_page_s_tree_is_left_as_it_came() -> None:
    sent = parts()
    notes(sent, SUBJECT)
    assert sent == parts()


@pytest.mark.parametrize("sent", [None, [], "moves", [1, 2]])
def test_a_page_without_a_move_tree_is_refused(sent: object) -> None:
    with pytest.raises(FetchError, match="carries no moves"):
        notes(sent, SUBJECT)


def test_blank_texts_are_no_notes() -> None:
    sent = [node(0, **gamebook(hint="  ", deviation=""))]
    assert notes(sent, SUBJECT) == []


def test_each_note_goes_after_the_comments_of_its_own_move() -> None:
    added = annotate(GAMEBOOK, notes(parts(), SUBJECT), SUBJECT)
    headers, moves = added.split("\n\n", 1)
    assert headers == GAMEBOOK.split("\n\n", 1)[0]
    assert moves == (
        "{ Find White's moves. } { Hint: Start in the center. } 1. e4 "
        "{ The center first. } "
        "{ When any other wrong move is played: Claim the center with a pawn. } "
        "1... e5 { Hint: What attacks e5? } 2. Nf3!? { Develops with a threat. } "
        "{ [%cal Gf3e5] } "
        "{ When any other wrong move is played: Develop, and attack something "
        "(while) you do. } "
        "(2. Bc4 { Also develops, but threatens nothing. }) "
        "(2. Qh5?! { Too early for the queen. } 2... Nc6 "
        "{ Hint: The queen can be hit. }) 2... Nc6 *"
    )


def test_a_move_forced_to_be_a_variation_counts_twice_in_the_export() -> None:
    # lila exports a first move forced to be a variation both as the mainline
    # and as the first variation, so the moves after it sit one place further
    # on in the export than in the tree. The tree's mainline stops before it.
    forced = node(3, "Nf3", forceVariation=True, children=[node(4, "Nc6")])
    queen = node(
        3, "Qh5", children=[node(4, "Nc6", **gamebook(hint="The queen can be hit."))]
    )
    sent = [node(0), node(1, "e4"), node(2, "e5", children=[forced, queen])]
    found = notes(sent, SUBJECT)
    assert found == [
        Note((0, 0, 2, 0), ("e4", "e5", "Qh5", "Nc6"), f"{HINT}The queen can be hit.")
    ]
    book = chapter(
        "Gb0ok123",
        "Forced",
        "1. e4 e5 2. Nf3 (2. Nf3 Nc6) (2. Qh5 Nc6) 2... Nc6 *",
        ChapterMode="gamebook",
    )
    added = annotate(book, found, SUBJECT)
    assert added.endswith(
        "(2. Nf3 Nc6) (2. Qh5 Nc6 { Hint: The queen can be hit. }) 2... Nc6 *"
    )
