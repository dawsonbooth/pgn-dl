"""Challenges: targets, themes and categories in order, one file per category."""

from __future__ import annotations

import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import FetchError, UnknownTarget
from pgn_dl.sources.chessendings import flight
from pgn_dl.sources.chessendings.challenges import (
    ChallengesExtractor,
    category_from,
    parse_challenges,
)

from .conftest import (
    ROOK_WIN,
    ROOT,
    FakeFetcher,
    category_page,
    challenge_pages,
    component,
    element,
    games,
    page,
    stream,
    theme_page,
)

INDEX = f"{ROOT}/challenges"


@pytest.mark.parametrize(
    "target",
    [
        "https://chessendings.com/challenges/rook-win",
        "https://chessendings.com/challenges/rook-win?tab=practice&level=2",
        "rook-win",
    ],
)
def test_targets(target: str) -> None:
    assert category_from(target) == "rook-win"


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("https://chessendings.com/challenges", "is all of the challenges"),
        ("https://chessendings.com/challenges/t/rook", "is a challenge theme"),
    ],
)
def test_the_challenges_or_a_theme_is_refused(target: str, message: str) -> None:
    with pytest.raises(WholeSection, match=f"{message}; give one category's URL"):
        category_from(target)


def test_the_registry_refuses_a_theme_s_url() -> None:
    with pytest.raises(WholeSection, match="is a challenge theme"):
        registry.resolve_url("https://chessendings.com/challenges/t/queen")


@pytest.mark.parametrize(
    "target",
    ["https://chessendings.com/challenges/t/", "https://example.com/challenges", ""],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(
        ValueError, match="could not read a chessendings challenge"
    ) as refused:
        category_from(target)
    assert not isinstance(refused.value, WholeSection)


def test_challenge_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url("https://chessendings.com/challenges/queen-mate")
    assert isinstance(found, ChallengesExtractor)
    assert found.category == "queen-mate"


@pytest.mark.parametrize(
    ("category", "path"),
    [
        ("queen-mate", ("01-checkmates", "01-queen-mate")),
        ("rook-win", ("02-rook", "01-rook-win")),
        ("rook-draw", ("02-rook", "02-rook-draw")),
    ],
)
async def test_themes_and_categories_are_numbered_in_the_site_order(
    category: str, path: tuple[str, str]
) -> None:
    fetcher = FakeFetcher(challenge_pages(), signed_in=False)
    collection, refs = await ChallengesExtractor(category).discover(fetcher)
    assert (collection.kind, collection.slug) == ("challenges", "endgame-challenges")
    assert [(r.id, r.path) for r in refs] == [(category, path)]
    # Discovery reads the index and the theme pages, no category.
    assert fetcher.gets == [INDEX, f"{INDEX}/t/checkmates", f"{INDEX}/t/rook"]


async def test_a_theme_page_that_lists_no_category_means_the_site_changed() -> None:
    pages = challenge_pages() | {f"{INDEX}/t/rook": theme_page([])}
    with pytest.raises(FetchError, match="theme 'rook' lists no category") as refused:
        await ChallengesExtractor("rook-win").discover(
            FakeFetcher(pages, signed_in=False)
        )
    assert not isinstance(refused.value, UnknownTarget)


async def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(UnknownTarget, match="no challenge 'pawn-win'"):
        await ChallengesExtractor("pawn-win").discover(
            FakeFetcher(challenge_pages(), signed_in=False)
        )


async def test_a_category_is_one_file_with_a_game_per_position() -> None:
    fetcher = FakeFetcher(challenge_pages(), signed_in=False)
    extractor = ChallengesExtractor("rook-win")
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn is not None
    positions = games(item.pgn)
    assert [g.headers["FEN"] for g in positions] == [fen for fen, _ in ROOK_WIN]
    assert [g.headers["Round"] for g in positions] == ["1", "2"]
    assert {g.headers["Event"] for g in positions} == {"Winning rook endings"}
    assert {g.headers["ChessendingsTheme"] for g in positions} == {"Rooks"}
    assert [g.comment for g in positions] == [
        "White to play and win: mate in 6.",
        "White to play and win.",
    ]
    assert (positions[0].headers["White"], positions[0].headers["Black"]) == (
        "Winning rook endings",
        "?",
    )


async def test_a_hold_condition_is_a_draw_to_hold() -> None:
    fetcher = FakeFetcher(challenge_pages(), signed_in=False)
    extractor = ChallengesExtractor("rook-draw")
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn is not None
    assert [g.comment for g in games(item.pgn)] == [
        "White to play and draw: hold 8 moves."
    ]


@pytest.mark.usefixtures("member")
async def test_a_saved_session_is_not_sent() -> None:
    # The positions are public; FakeFetcher fails any request carrying the
    # session when told the run is not signed in.
    fetcher = FakeFetcher(challenge_pages(), signed_in=False)
    extractor = ChallengesExtractor("rook-draw")
    _, refs = await extractor.discover(fetcher)
    await extractor.fetch(fetcher, refs[0])


def test_only_the_positions_list_is_read_not_the_preview() -> None:
    tree = flight.parse(
        category_page("rook-win", "Rooks", "Winning rook endings", ROOK_WIN)
    )
    assert [c.number for c in parse_challenges(tree, "rook-win")] == [1, 2]


def test_a_condition_this_module_cannot_read_fails_the_category() -> None:
    tree = flight.parse(
        category_page(
            "x", "T", "X", [("8/8/8/8/8/8/1k6/K6Q w - - 0 1", "Position 1 · Survive")]
        )
    )
    with pytest.raises(FetchError, match="sets 'Survive'"):
        parse_challenges(tree, "x")


async def test_a_category_page_without_its_breadcrumb_fails() -> None:
    pages = challenge_pages()
    pages[f"{INDEX}/rook-draw?tab=practice"] = stream(page())
    fetcher = FakeFetcher(pages, signed_in=False)
    extractor = ChallengesExtractor("rook-draw")
    _, refs = await extractor.discover(fetcher)
    with pytest.raises(FetchError, match="no breadcrumb"):
        await extractor.fetch(fetcher, refs[0])


@pytest.mark.parametrize(
    "props",
    [
        {"fen": "not a fen", "caption": "Position 1 · Win"},
        {"fen": "8/8/8/8/8/8/1k6/K6Q w - - 0 1", "caption": "Win"},
    ],
)
def test_an_unreadable_position_fails_the_category(props: dict) -> None:
    tree = flight.parse(
        stream(
            page(
                element(
                    "ol", {"children": [element("li", {"children": component(props)})]}
                )
            )
        )
    )
    with pytest.raises(FetchError):
        parse_challenges(tree, "x")


def test_a_page_without_positions_fails_the_category() -> None:
    with pytest.raises(FetchError, match="no positions"):
        parse_challenges(flight.parse(stream(page())), "x")
