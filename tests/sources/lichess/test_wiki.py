"""An opening page's description, read from the page's wiki block.

The pages are invented in the shape lila serves (``OpeningUi.show``,
``WikiUi``, the wiki markdown as flexmark renders it); none of the text is
Lichess's.
"""

from __future__ import annotations

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess.wiki import description

URL = "https://lichess.org/opening/Some_Defense"


def page(wiki: str) -> str:
    """An opening page around ``wiki``, the inside of ``.opening__wiki``."""
    return (
        '<!DOCTYPE html><html lang="en-US"><head><title>Opening • Some Defense • '
        "lichess.org</title></head><body>"
        '<main class="page box box-pad opening">'
        '<h1 class="opening__title"><a href="/opening" data-icon=""></a>'
        '<span class="opening__name"><span class="opening__name__section '
        'opening__name__section--1">Some Defense</span></span></h1>'
        '<div class="opening__intro"><div class="opening__intro__result-lpv">'
        '<div class="opening__intro__result result-bar"></div>'
        '<div class="lpv lpv--todo lpv--moves-bottom is2d" data-pgn="e4 c5" '
        'data-title="Some Defense"><div class="lpv__board"></div></div></div>'
        '<div class="opening__intro__content">'
        f'<div class="opening__wiki">{wiki}</div>'
        '<div class="opening__popularity-actions"><div class="opening__actions">'
        '<a class="button text" href="/analysis/e4_c5#explorer">Opening explorer</a>'
        "</div></div></div></div>"
        '<div class="opening__panels"><p>Please log in to load more opening '
        "pages.</p></div></main></body></html>"
    )


def markup(inner: str) -> str:
    return page(f'<div class="opening__wiki__markup">{inner}</div>')


def test_the_placeholder_means_no_description() -> None:
    placeholder = (
        '<div class="opening__wiki__markup__placeholder">'
        "No description of the opening, yet.</div>"
    )
    assert description(markup(placeholder), URL) is None


def test_paragraphs_become_paragraphs() -> None:
    html = markup(
        "<p>Black meets the pawn from the side, and the game turns "
        "asymmetrical early.</p>\n"
        "<p>White&#39;s usual answer is to open the centre &amp; develop "
        "fast.</p>"
    )
    assert description(html, URL) == (
        "Black meets the pawn from the side, and the game turns asymmetrical "
        "early.\n\n"
        "White's usual answer is to open the centre & develop fast."
    )


def test_the_last_move_s_note_and_a_rule_read_as_paragraphs() -> None:
    # lila turns the page's own move note into a <p> inside the list and
    # empties the other notes' lines, leaving blank lines behind.
    html = markup(
        "<ul>\n<p>The pawn on c5 keeps White from a free d4.</p>\n\n\n\n</ul>\n"
        "<hr>\n<p>A counterattacking choice.</p>"
    )
    assert description(html, URL) == (
        "The pawn on c5 keeps White from a free d4.\n\nA counterattacking choice."
    )


def test_headers_lists_and_inline_markup_read_as_text() -> None:
    html = markup(
        '<h2><a href="#main-ideas" id="main-ideas"></a>Main ideas</h2>\n'
        "<p>Play for <strong>d5</strong> and <em>then</em> see "
        '<a href="https://lichess.org/study/x" rel="nofollow noreferrer">this '
        "study</a>.</p>\n"
        "<ul>\n<li>Develop the knight.</li>\n<li>Castle short.<br>Then f5.</li>\n"
        "</ul>\n<p>Last word.</p>"
    )
    assert description(html, URL) == (
        "Main ideas\n\n"
        "Play for d5 and then see this study.\n\n"
        "- Develop the knight.\n"
        "- Castle short.\nThen f5.\n\n"
        "Last word."
    )


def test_a_loose_list_item_is_still_one_item() -> None:
    html = markup("<ul>\n<li>\n<p>First idea.</p>\n</li>\n<li>Second.</li>\n</ul>")
    assert description(html, URL) == "- First idea.\n- Second."


def test_a_numbered_list_keeps_its_numbers() -> None:
    # lila's markdown keeps "1)" lists as lists; "1." it writes out as text.
    html = markup('<ol start="3">\n<li>Third.</li>\n<li>Fourth.</li>\n</ol>')
    assert description(html, URL) == "3. Third.\n4. Fourth."


def test_whitespace_from_the_source_is_not_text() -> None:
    html = markup("<p>  Spread\n   over   lines\n</p>")
    assert description(html, URL) == "Spread over lines"


def test_nothing_after_the_wiki_block_is_read() -> None:
    # The page's other text (actions, the panels' log-in note) is not the
    # description.
    text = description(markup("<p>Only this.</p>"), URL)
    assert text == "Only this."


def test_a_wiki_block_left_empty_means_no_description() -> None:
    # Every note in the list was for another move.
    assert description(markup("<ul>\n\n\n</ul>"), URL) is None


def test_a_page_with_no_wiki_block_is_refused() -> None:
    # Not an opening page, or one whose shape has changed: writing the file
    # without a description would leave it skipped on every later run.
    with pytest.raises(FetchError, match="Some_Defense"):
        description("<html><body><p>Something else</p></body></html>", URL)
