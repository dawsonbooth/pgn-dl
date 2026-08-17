"""The shared HTML-collapsing helper, through the MoveTrainer interface."""

from __future__ import annotations

from pgn_dl.movetrainer import strip_html


def test_strip_html_keeps_the_text_after_the_last_tag_or_entity() -> None:
    # HTMLParser withholds a trailing entity until it knows the entity ended.
    assert strip_html("White is better &") == "White is better &"
    assert strip_html("<b>Careful</b>: the pawn falls") == "Careful: the pawn falls"


def test_strip_html_keeps_a_less_than_sign_that_opens_no_tag() -> None:
    # Comparisons are ordinary in chess prose, and the sites' editors do not
    # escape them; HTMLParser reads one as an unterminated tag and eats the
    # rest of the comment looking for its ">".
    assert strip_html("a<b in the pawn race, so White wins easily.") == (
        "a<b in the pawn race, so White wins easily."
    )
    assert strip_html("5<6>4") == "5<6>4"
    assert strip_html("<i>still</i> a tag") == "still a tag"


def test_strip_html_keeps_the_breaks_it_inserts() -> None:
    assert strip_html("<p>One.</p><p>Two.</p>") == "One.\n\nTwo."
    assert strip_html("a<br>b") == "a\nb"
    assert strip_html("keeps   one   space") == "keeps one space"
