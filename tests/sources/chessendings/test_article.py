"""Articles: the hero, the body, and its blocks in page order."""

from __future__ import annotations

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessendings import flight
from pgn_dl.sources.chessendings.article import (
    Heading,
    Lesson,
    Paragraph,
    Replay,
    read_article,
)

from .conftest import (
    article_page,
    element,
    embedded_lesson,
    page,
    paragraph,
    replay,
    section,
    static_figure,
    stream,
)

KQK = "8/8/3k4/8/8/2Q1K3/8/8 w - - 0 1"


def _read(text: str):
    return read_article(flight.parse(text), "guide x")


def test_the_hero_is_the_title_and_the_paragraphs_under_it() -> None:
    article = _read(
        article_page(
            "Zugzwang", ["When moving hurts", "The definition."], [replay(KQK, [])]
        )
    )
    # Not the not-found page's h1, not the date stamp, not the breadcrumbs.
    assert article.title == "Zugzwang"
    assert article.lede == ("When moving hurts", "The definition.")


def test_the_body_is_read_in_page_order() -> None:
    body = [
        replay(KQK, ["Ke4"], "The hero diagram.", pov="b"),
        section("Your turn", embedded_lesson("opposition-basics", KQK, "Opposition")),
        section(
            "The idea",
            paragraph(
                "Keep ", element("strong", {"children": "both"}), " kings close."
            ),
            static_figure("A picture only."),
        ),
    ]
    blocks = _read(article_page("T", ["Lede."], body)).blocks
    hero, *rest = blocks
    assert isinstance(hero, Replay)
    assert (hero.fen, hero.moves, hero.caption, hero.pov) == (
        KQK,
        ("Ke4",),
        "The hero diagram.",
        "b",
    )
    assert rest == [
        Heading("Your turn"),
        Lesson("opposition-basics", "Opposition", KQK),
        Heading("The idea"),
        Paragraph("Keep both kings close."),
        Paragraph("A picture only."),
    ]


def test_a_table_is_its_caption_then_a_line_per_row() -> None:
    table = element(
        "table",
        {
            "children": [
                element("caption", {"children": "Draw rules"}),
                element(
                    "thead",
                    {
                        "children": element(
                            "tr",
                            {
                                "children": [
                                    element("th", {"children": "Rule"}),
                                    element("th", {"children": "Claimed"}),
                                ]
                            },
                        )
                    },
                ),
                element(
                    "tbody",
                    {
                        "children": [
                            element(
                                "tr",
                                {
                                    "children": [
                                        element("th", {"children": ["Repetition"]}),
                                        element("td", {"children": ["Yes"]}),
                                    ]
                                },
                            )
                        ]
                    },
                ),
            ]
        },
    )
    swipe = paragraph("Swipe sideways to see every column", className="mt-4 sm:hidden")
    blocks = _read(article_page("T", [], [replay(KQK, []), swipe, table])).blocks
    # The hint a narrow screen shows is chrome, not text.
    assert blocks[1:] == (Paragraph("Draw rules\nRule | Claimed\nRepetition | Yes"),)


def test_what_follows_the_article_is_not_read() -> None:
    promo = element(
        "section", {"children": [section("Keep reading", paragraph("Another guide."))]}
    )
    blocks = _read(article_page("T", [], [replay(KQK, [])], promo=(promo,))).blocks
    assert not any(isinstance(b, Paragraph) for b in blocks)


def test_annotations_are_kept_per_frame() -> None:
    figure = replay(
        KQK,
        ["Ke4", "Ke6"],
        annotations=[{"highlightSquares": ["e4"], "arrows": None, "zones": None}, None],
    )
    (diagram,) = _read(article_page("T", [], [figure])).blocks
    assert isinstance(diagram, Replay)
    assert diagram.annotations == (
        {"highlightSquares": ["e4"], "arrows": None, "zones": None},
        None,
    )


def test_a_page_without_an_article_is_refused() -> None:
    with pytest.raises(FetchError, match="no article"):
        _read(stream(page(element("h1", {"children": "T"}))))


def test_a_diagram_with_a_move_that_is_not_text_is_refused() -> None:
    moves: list = ["Ke4", 3]
    with pytest.raises(FetchError, match="not text"):
        _read(article_page("T", [], [replay(KQK, moves)]))
