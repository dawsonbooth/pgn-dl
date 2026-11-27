"""Guides: targets, the index, layout, and an article as games."""

from __future__ import annotations

import chess
import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import Collection
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.sources.chessendings import flight
from pgn_dl.sources.chessendings.article import read_article
from pgn_dl.sources.chessendings.guides import (
    GuidesExtractor,
    guide_games,
    parse_index,
)
from pgn_dl.sources.chessendings.section import layout

from .conftest import (
    ROOT,
    FakeFetcher,
    article_page,
    component,
    element,
    embedded_lesson,
    games,
    page,
    paragraph,
    replay,
    section,
    static_figure,
    stream,
)

INDEX = f"{ROOT}/guides"
#: Mutual zugzwang: White to move only draws.
ZUGZWANG = "8/4k3/8/4K3/4P3/8/8/8 w - - 0 1"
TREBUCHET = "8/8/8/2Kp4/3Pk3/8/8/8 w - - 0 1"
KQK = "8/8/3k4/8/8/2Q1K3/8/8 w - - 0 1"


@pytest.mark.parametrize(
    ("target", "slug"),
    [
        ("https://chessendings.com/guides/zugzwang", "zugzwang"),
        ("https://chessendings.com/fr/guides/zugzwang#faq", "zugzwang"),
        ("zugzwang", "zugzwang"),
    ],
)
def test_targets(target: str, slug: str) -> None:
    assert GuidesExtractor(target).slug == slug


@pytest.mark.parametrize(
    "target", ["https://chessendings.com/guides", "https://chessendings.com/guides/"]
)
def test_the_guides_page_is_refused(target: str) -> None:
    with pytest.raises(
        WholeSection, match="is all of the guides; give one guide's URL or slug"
    ):
        GuidesExtractor(target)


def test_the_registry_refuses_the_guides_page() -> None:
    with pytest.raises(WholeSection, match="is all of the guides"):
        registry.resolve_url("https://chessendings.com/guides")


@pytest.mark.parametrize(
    "target",
    ["https://example.com/guides/zugzwang", "https://chessendings.com/train", ""],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(
        ValueError, match="could not read a chessendings guide"
    ) as refused:
        GuidesExtractor(target)
    assert not isinstance(refused.value, WholeSection)


def test_guide_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url("https://chessendings.com/guides/opposition")
    assert isinstance(found, GuidesExtractor) and found.slug == "opposition"


def _card(slug: str) -> list:
    return component(
        {"href": f"/guides/{slug}", "children": element("h3", {"children": slug})}
    )


def index_page() -> str:
    """The index: a guide featured above the chapters, then the chapters."""
    chapters = [
        ("Checkmates", ["how-to-checkmate", "back-rank-mate"]),
        ("Rules & concepts", ["zugzwang"]),
    ]
    sections = [
        element(
            "section",
            {
                "children": [
                    element("h2", {"children": title}),
                    *(_card(s) for s in slugs),
                ]
            },
        )
        for title, slugs in chapters
    ]
    featured = _card("zugzwang")
    return stream(
        page(component({"href": "/guides", "children": "Guides"}), featured, *sections)
    )


def test_guides_are_numbered_within_their_chapters() -> None:
    # The featured link above the chapters numbers nothing.
    placed = layout(parse_index(flight.parse(index_page())), width=2)
    assert [(x.slug, x.group, path) for x, path in placed] == [
        ("how-to-checkmate", "Checkmates", ("01-checkmates", "01-how-to-checkmate")),
        ("back-rank-mate", "Checkmates", ("01-checkmates", "02-back-rank-mate")),
        ("zugzwang", "Rules & concepts", ("02-rules-concepts", "01-zugzwang")),
    ]


def test_an_index_without_guides_is_refused() -> None:
    with pytest.raises(FetchError, match="no chessendings guides"):
        parse_index(flight.parse(stream(page())))


def zugzwang_page() -> str:
    body = [
        replay(ZUGZWANG, ["Kd5", "Kd7"], "White to move only draws."),
        section("Your turn: play it", embedded_lesson("opposition-basics", KQK)),
        section("What does it mean?", paragraph("Being forced to move.")),
        section(
            "Trébuchet",
            paragraph("Whoever moves loses."),
            replay(TREBUCHET, ["Kb4", "Kxd4"], "White to move loses the pawn."),
            paragraph("And the same with Black to move."),
        ),
        section("FAQ", element("h3", {"children": "Can you pass?"}), paragraph("No.")),
    ]
    return article_page(
        "Zugzwang in Chess", ["When moving hurts", "A definition."], body
    )


async def _download(slug: str, pages: dict[str, str]) -> list[chess.pgn.Game]:
    extractor = GuidesExtractor(slug)
    fetcher = FakeFetcher(pages, signed_in=False)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn is not None
    return games(item.pgn)


async def test_a_guide_is_a_game_per_line_with_the_text_around_it() -> None:
    pages = {INDEX: index_page(), f"{INDEX}/zugzwang": zugzwang_page()}
    hero, trebuchet = await _download("zugzwang", pages)
    assert [g.headers["Round"] for g in (hero, trebuchet)] == ["1", "2"]
    assert {g.headers["Event"] for g in (hero, trebuchet)} == {"Zugzwang in Chess"}
    assert hero.headers["ChessendingsTheme"] == "Rules & concepts"
    # The lesson the guide asks the reader to play.
    assert hero.headers["ChessendingsLesson"] == "opposition-basics"
    # The first game opens with the guide's own opening paragraphs.
    assert (
        hero.comment
        == "When moving hurts\n\nA definition.\n\nWhite to move only draws."
    )
    # A section without a line follows the game before it, after the line; a
    # heading with nothing under it (the embedded lesson's) is dropped.
    assert (
        list(hero.mainline())[-1].comment
        == "What does it mean?\n\nBeing forced to move."
    )
    # A section's own line leads with its heading and text.
    assert trebuchet.comment == (
        "Trébuchet\n\nWhoever moves loses.\n\nWhite to move loses the pawn."
    )
    # What follows the line in its section, and the FAQ after it, end the guide.
    assert list(trebuchet.mainline())[-1].comment == (
        "And the same with Black to move.\n\nFAQ\n\nCan you pass?\n\nNo."
    )
    assert [m.uci() for m in trebuchet.mainline_moves()] == ["c5b4", "e4d4"]


def test_frame_drawings_ride_on_their_positions() -> None:
    figure = replay(
        KQK,
        ["Ke4", "Ke6", "Qe5+"],
        annotations=[
            {
                "highlightSquares": ["e4"],
                "arrows": [{"from": "e3", "to": "e4", "kind": "move"}],
            },
            None,
            {"zones": [{"from": "a6", "to": "b7"}]},
        ],
    )
    article = read_article(flight.parse(article_page("K+Q", [], [figure])), "x")
    (item,) = guide_games(article, slug="x", chapter="Checkmates")
    collection = Collection(source="chessendings", kind="guides", slug="g", name="G")
    (game,) = games(game_to_pgn(collection, item))
    # Frame 0 is the starting position, frame 2 the position after two moves.
    assert game.comment == "[%cal Ge3e4] [%csl Ye4]"
    ke4, ke6, qe5 = list(game.mainline())
    assert (ke4.comment, ke6.comment, qe5.comment) == ("", "[%csl Ba6,Bb6,Ba7,Bb7]", "")


def test_an_unplayable_line_fails_the_guide() -> None:
    article = read_article(
        flight.parse(article_page("T", [], [replay(ZUGZWANG, ["Kd5", "Kd9"])])), "x"
    )
    with pytest.raises(FetchError, match="'Kd9', illegal"):
        guide_games(article, slug="x", chapter="C")


def test_a_guide_without_lines_is_one_game_at_its_lessons_position() -> None:
    body = [
        static_figure("A picture of the corner."),
        section("Your turn", embedded_lesson("two-knights", KQK)),
        section("The method", paragraph("Three steps.")),
    ]
    article = read_article(flight.parse(article_page("Knights", ["Lede."], body)), "x")
    (item,) = guide_games(article, slug="x", chapter="Checkmates")
    assert item.starting_fen == KQK
    assert (
        item.comment
        == "Lede.\n\nA picture of the corner.\n\nThe method\n\nThree steps."
    )
    assert item.root.children == []
    assert item.headers["ChessendingsLesson"] == "two-knights"


def test_a_guide_without_lines_or_a_lesson_is_at_the_standard_position() -> None:
    body = [
        static_figure("A table of rules."),
        section("Rules", paragraph("Fifty moves.")),
    ]
    article = read_article(flight.parse(article_page("50-move rule", [], body)), "x")
    (item,) = guide_games(article, slug="x", chapter="Rules")
    assert item.starting_fen == chess.STARTING_FEN
    assert "ChessendingsLesson" not in item.headers


async def test_a_guide_gets_the_path_the_index_gives_it() -> None:
    _, refs = await GuidesExtractor("zugzwang").discover(
        FakeFetcher({INDEX: index_page()}, signed_in=False)
    )
    assert [(r.id, r.path) for r in refs] == [
        ("zugzwang", ("02-rules-concepts", "01-zugzwang"))
    ]


async def test_an_unlisted_guide_is_refused() -> None:
    with pytest.raises(FetchError, match="no guide 'nope'"):
        await GuidesExtractor("nope").discover(
            FakeFetcher({INDEX: index_page()}, signed_in=False)
        )


@pytest.mark.usefixtures("member")
async def test_a_saved_session_is_not_sent() -> None:
    # The guides are public; FakeFetcher fails any request carrying the
    # session when told the run is not signed in.
    pages = {INDEX: index_page(), f"{INDEX}/zugzwang": zugzwang_page()}
    assert len(await _download("zugzwang", pages)) == 2
