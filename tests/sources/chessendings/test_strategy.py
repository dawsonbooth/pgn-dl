"""Strategy: targets, the members-only listing, layout, and Pro positions."""

from __future__ import annotations

import io

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.sources.chessendings import flight
from pgn_dl.sources.chessendings.pages import ProRequired, SignedOut
from pgn_dl.sources.chessendings.section import layout
from pgn_dl.sources.chessendings.strategy import StrategyExtractor

from .conftest import (
    JAILED_FEN,
    ROOT,
    FakeFetcher,
    component,
    page,
    position_page,
    strategy_listing,
    stream,
)

LISTING = f"{ROOT}/practice"
JAILED = "the-pawn-that-jails-a-rook"


def position_url(slug: str) -> str:
    return f"{ROOT}/practice/{slug}"


@pytest.mark.parametrize(
    ("target", "slug"),
    [
        (
            "https://chessendings.com/practice/the-pawn-that-jails-a-rook",
            "the-pawn-that-jails-a-rook",
        ),
        ("the-pawn-that-jails-a-rook", "the-pawn-that-jails-a-rook"),
    ],
)
def test_targets(target: str, slug: str) -> None:
    assert StrategyExtractor(target).slug == slug


@pytest.mark.parametrize(
    "target",
    ["https://chessendings.com/practice", "https://chessendings.com/practice/?theme=x"],
)
def test_strategy_s_own_url_is_refused(target: str) -> None:
    with pytest.raises(
        WholeSection, match="is all of Strategy; give one Strategy position's"
    ):
        StrategyExtractor(target)


def test_the_registry_refuses_strategy_s_own_url() -> None:
    with pytest.raises(WholeSection, match="is all of Strategy"):
        registry.resolve_url("https://chessendings.com/practice")


@pytest.mark.parametrize(
    "target", ["https://chessendings.com/train/x", "https://example.com/practice", ""]
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(
        ValueError, match="could not read a chessendings Strategy position"
    ) as refused:
        StrategyExtractor(target)
    assert not isinstance(refused.value, WholeSection)


def test_strategy_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url(
        "https://chessendings.com/practice/walk-into-the-checks"
    )
    assert isinstance(found, StrategyExtractor) and found.slug == "walk-into-the-checks"


def test_positions_are_numbered_within_ideas_in_the_site_order() -> None:
    tree = flight.parse(strategy_listing())
    placed = layout(StrategyExtractor(JAILED).parse_listing(tree))
    assert [(x.slug, path) for x, path in placed] == [
        (
            "the-pawn-that-jails-a-rook",
            ("01-king-centralization", "001-the-pawn-that-jails-a-rook"),
        ),
        (
            "walk-into-the-checks",
            ("01-king-centralization", "002-walk-into-the-checks"),
        ),
        ("two-weaknesses-win", ("02-two-weaknesses", "001-two-weaknesses-win")),
    ]
    jailed, walk = placed[0][0], placed[1][0]
    assert jailed.headers["ChessendingsOrigin"] == "Larsen - Browne"
    assert jailed.preface == "From Larsen - Browne."
    assert (jailed.locked, walk.locked) == (False, True)
    # No origin, no header and no preface.
    assert "ChessendingsOrigin" not in walk.headers and walk.preface == ""


async def test_strategy_needs_a_saved_session() -> None:
    # The real session lookup, against an empty profile: the run ends in the
    # credential store's own hint rather than in an anonymous listing.
    fetcher = FakeFetcher({}, signed_in=True)
    with pytest.raises(FetchError, match="run: pgn-dl login chessendings"):
        await StrategyExtractor(JAILED).discover(fetcher)
    assert fetcher.gets == []


@pytest.mark.usefixtures("member")
async def test_a_listing_rendered_for_a_guest_is_refused() -> None:
    guest_view = stream(page(component({"title": "Strategy needs an account"})))
    with pytest.raises(SignedOut):
        await StrategyExtractor(JAILED).discover(
            FakeFetcher({LISTING: guest_view}, signed_in=True)
        )


@pytest.mark.usefixtures("member")
async def test_a_position_is_its_board_plan_and_origin() -> None:
    pages = {LISTING: strategy_listing(), position_url(JAILED): position_page()}
    fetcher = FakeFetcher(pages, signed_in=True)
    extractor = StrategyExtractor(JAILED)
    collection, refs = await extractor.discover(fetcher)
    assert (collection.kind, collection.slug) == ("strategy", "strategy")
    item = await extractor.fetch(fetcher, refs[0])
    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None and not game.errors
    assert game.headers["FEN"] == JAILED_FEN
    assert game.headers["ChessendingsTheme"] == "King centralization"
    assert game.headers["ChessendingsOrigin"] == "Larsen - Browne"
    assert game.comment == (
        "White to play and win.\n\nFrom Larsen - Browne.\n\n"
        "Your pawn chains his rook to b8. [%csl Yb7,Yb8]"
    )
    assert list(game.mainline_moves()) == []


@pytest.mark.usefixtures("member")
async def test_a_position_is_numbered_from_the_whole_listing() -> None:
    fetcher = FakeFetcher({LISTING: strategy_listing()}, signed_in=True)
    _, refs = await StrategyExtractor("two-weaknesses-win").discover(fetcher)
    assert [(r.id, r.path) for r in refs] == [
        ("two-weaknesses-win", ("02-two-weaknesses", "001-two-weaknesses-win"))
    ]


@pytest.mark.usefixtures("member")
async def test_a_locked_position_fails_without_a_request() -> None:
    fetcher = FakeFetcher({LISTING: strategy_listing()}, signed_in=True)
    extractor = StrategyExtractor("walk-into-the-checks")
    _, refs = await extractor.discover(fetcher)
    with pytest.raises(ProRequired, match="this account does not have Pro"):
        await extractor.fetch(fetcher, refs[0])
    assert fetcher.gets == [LISTING]
