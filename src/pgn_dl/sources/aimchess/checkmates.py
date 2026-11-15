"""aimchess's Checkmate Patterns module.

A fixed set of 35 flashcards, one per named mating pattern (Anastasia's Mate,
Boden's Mate, ...), each a position, the opponent's move and the mating line.
The site shows them shuffled; the files follow the order the API returns them
in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef, MoveNode
from ...core.paths import slugify
from . import auth
from .client import Client
from .pgn import SITE

#: The module's title on the site.
COLLECTION_NAME = "Checkmate Patterns"

#: The module page, or one of the app's pages under it.
_URL = re.compile(
    r"^https?://(www\.)?aimchess\.com/modules/checkmate_flashcards(?:[/?#]|$)",
    re.IGNORECASE,
)


def _title(card: Mapping[str, Any]) -> str:
    title = card.get("title")
    return title if isinstance(title, str) else ""


def card_to_game(card: Mapping[str, Any]) -> GameItem:
    """One flashcard as a game: the opponent's move, then the mating line."""
    title = _title(card)
    fen, first, solution = card.get("fen"), card.get("firstMove"), card.get("solution")
    if not (
        isinstance(fen, str) and isinstance(first, str) and isinstance(solution, str)
    ):
        raise FetchError(f"aimchess checkmate pattern {title!r} is incomplete")
    root = node = MoveNode(san=None)
    for san in [first, *solution.split()]:
        child = MoveNode(san=san)
        node.children.append(child)
        node = child
    # The player is the side that mates: the one replying to the first move.
    player = "Black" if chess.Board(fen).turn == chess.WHITE else "White"
    headers = {"Event": title, "White": "?", "Black": "?", player: title}
    return GameItem(headers=headers, starting_fen=fen, root=root)


class CheckmatePatternsExtractor(Extractor[list[Any]]):
    """The Checkmate Patterns module, one file per pattern."""

    key = "aimchess:checkmate-patterns"
    url_patterns = (_URL,)

    def __init__(self, target: str) -> None:
        super().__init__()
        if _URL.match(target.strip()) is None:
            raise ValueError(f"not the aimchess Checkmate Patterns URL: {target!r}")

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], list[Any]]:
        client = Client(fetcher, token=auth.load_token())
        cards = (await client.checkmate_patterns()).get("problems")
        if not isinstance(cards, list) or not cards:
            raise FetchError("aimchess returned no checkmate patterns")
        refs = [
            ItemRef(id=str(n), path=(f"{n:03d}-{slugify(_title(card), 'pattern')}",))
            for n, card in enumerate(cards, start=1)
        ]
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME, headers={"Site": SITE}
        )
        return collection, refs, cards

    async def _fetch(
        self, context: list[Any], fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        # Discovery already holds every card; nothing more is requested.
        return card_to_game(context[int(ref.id) - 1])
