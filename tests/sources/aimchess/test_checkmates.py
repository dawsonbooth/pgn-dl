"""Checkmate Patterns: target, discovery, and each flashcard as a game."""

from __future__ import annotations

import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.sources.aimchess.checkmates import CheckmatePatternsExtractor, card_to_game

from .conftest import CARDS, CHECKMATES_URL, FakeFetcher

pytestmark = pytest.mark.usefixtures("stub_token")

URL = "https://aimchess.com/modules/checkmate_flashcards"


def test_the_module_url_resolves_to_this_extractor() -> None:
    assert isinstance(registry.resolve_url(URL), CheckmatePatternsExtractor)
    assert isinstance(registry.resolve_url(URL + "/review"), CheckmatePatternsExtractor)
    assert CheckmatePatternsExtractor.from_url(URL + "_x") is None
    with pytest.raises(ValueError):
        CheckmatePatternsExtractor("https://aimchess.com/modules/debut_master")


async def test_every_card_is_listed_in_api_order() -> None:
    fetcher = FakeFetcher()
    extractor = CheckmatePatternsExtractor(URL)
    collection, refs = await extractor.discover(fetcher)
    assert (collection.kind, collection.slug) == (
        "checkmate-patterns",
        "checkmate-patterns",
    )
    assert [r.path for r in refs] == [("001-anastasia-s-mate",), ("002-legal-s-mate",)]
    assert fetcher.gets == [CHECKMATES_URL]
    # Discovery already has every card.
    await extractor.fetch(fetcher, refs[1])
    assert fetcher.gets == [CHECKMATES_URL]


def test_a_card_is_the_opponents_move_then_the_mate() -> None:
    item = card_to_game(CARDS[1])
    text = game_to_pgn(
        CheckmatePatternsExtractor.collection(slug="s", name="S", headers={}), item
    )
    assert "1... Bxd1 2. Bxf7+ Ke7 3. Nd5#" in text
    # The player is the side that mates.
    assert '[White "Legal\'s Mate"]' in text and '[Black "?"]' in text


def test_an_incomplete_card_fails_its_own_item() -> None:
    with pytest.raises(FetchError, match="Anastasia's Mate"):
        card_to_game(CARDS[0] | {"solution": None})
