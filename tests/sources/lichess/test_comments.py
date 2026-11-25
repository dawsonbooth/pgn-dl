"""Comments added to a chapter's export, the rest of it left as it came.

The chapters are invented in the shape Lichess's export writes them; none of
the text is Lichess's.
"""

from __future__ import annotations

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess.comments import Note, annotate

from .conftest import SACRIFICE, chapter

SUBJECT = "lichess study h7Sac1fz chapter Gb0ok123"

MOVES = (
    "{ Find White's moves. } 1. e4 { The center first. } 1... e5 2. Nf3!? "
    "{ Develops. } (2. Bc4 { Also develops. }) 2... Nc6 *"
)
CHAPTER = chapter("Gb0ok123", "Develop", MOVES)


def moves_of(pgn: str) -> str:
    headers, moves = pgn.split("\n\n", 1)
    assert headers == CHAPTER.split("\n\n", 1)[0]
    return moves


def test_intro_texts_come_first_in_their_order() -> None:
    added = annotate(CHAPTER, [], SUBJECT, intro=["The study.", "The chapter."])
    assert moves_of(added) == "{ The study. } { The chapter. } " + MOVES


def test_intro_comes_before_a_root_note_of_a_chapter_with_no_comment() -> None:
    bare = chapter("Gb0ok123", "Opening move", "1. e4 *")
    found = [Note((), (), "Hint: Start in the center.")]
    added = annotate(bare, found, SUBJECT, intro=["Play the first move."])
    assert added.endswith(
        "\n\n{ Play the first move. } { Hint: Start in the center. } 1. e4 *"
    )


def test_intro_and_notes_together_leave_the_rest_as_it_was() -> None:
    found = [Note((0, 0, 0), ("e4", "e5", "Nf3"), "Hint: develop.")]
    added = annotate(CHAPTER, found, SUBJECT, intro=["About this."])
    assert moves_of(added) == (
        "{ About this. } { Find White's moves. } 1. e4 { The center first. } "
        "1... e5 2. Nf3!? { Develops. } { Hint: develop. } "
        "(2. Bc4 { Also develops. }) 2... Nc6 *"
    )


def test_an_intro_before_a_chapter_with_no_moves_precedes_its_result() -> None:
    # Practice chapters export as a position and " *".
    empty = chapter("Gb0ok123", "Mate", " *", fen="8/8/3k4/8/8/4K3/8/Q6R w - - 0 1")
    added = annotate(empty, [], SUBJECT, intro=["Mate in 3."])
    assert added.endswith("\n\n { Mate in 3. } *")


def test_braces_in_a_text_cannot_end_its_comment_early() -> None:
    added = annotate(CHAPTER, [], SUBJECT, intro=["A {brace} or two}"])
    assert moves_of(added).startswith("{ A (brace) or two) } { Find")


def test_a_move_python_chess_cannot_play_is_no_obstacle() -> None:
    # The export is only added to, so an unplayable sideline stays as it is.
    found = [Note((0,), ("Bxh7+",), "Hint: Take with check.")]
    added = annotate(SACRIFICE, found, SUBJECT)
    assert "1. Bxh7+ { Hint: Take with check. } Kxh7" in added
    assert "(1... Kf8) *" in added


@pytest.mark.parametrize(
    "note",
    [
        Note((0,), ("d4",), "Hint: the wrong move here"),
        Note((0, 0, 5), ("e4", "e5", "Nf3"), "Hint: no such variation"),
        Note((0, 0, 0, 0, 0), ("e4", "e5", "Nf3", "Nc6", "d4"), "Hint: past the end"),
        Note((), ("e4",), "Hint: a move for the root"),
        # The last move fits, but the way there does not.
        Note((0, 0, 1), ("e4", "e6", "Bc4"), "Hint: another line"),
    ],
)
def test_a_note_that_does_not_fit_the_moves_is_refused(note: Note) -> None:
    with pytest.raises(FetchError, match="gamebook notes do not fit its moves"):
        annotate(CHAPTER, [note], SUBJECT)


def test_nothing_to_add_leaves_the_export_as_it_is() -> None:
    assert annotate(CHAPTER, [], SUBJECT) is CHAPTER


def test_an_export_whose_variations_do_not_close_is_refused() -> None:
    broken = chapter("Gb0ok123", "Broken", "1. e4 (1. d4 *")
    unopened = chapter("Gb0ok123", "Broken", "1. e4 ) *")
    note = [Note((0,), ("e4",), "Hint: x")]
    # An unclosed variation still places a note on the mainline before it.
    assert "1. e4 { Hint: x }" in annotate(broken, note, SUBJECT)
    with pytest.raises(FetchError, match="not readable PGN"):
        annotate(unopened, note, SUBJECT)
