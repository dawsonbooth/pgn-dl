"""The aimchess quiz.

The quiz at /public/quiz draws on a fixed set of 100 puzzles: five in each of
five categories (opening, middlegame, endgame, strategy, tactics) at each of
four levels of difficulty. One quiz asks the 25 puzzles of one level. Each
puzzle's PGN gives the best move and, as a variation, a wrong one, both
explained. The set is compiled into the site's JavaScript, so no login is
needed. Each category is written as one file of 20 games, easiest first.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from .bundle import STRING, find_script, string_value
from .pgn import SITE, rewrite

#: The quiz page's heading.
COLLECTION_NAME = "Aimchess Quiz"

_URL = re.compile(
    r"^https?://(www\.)?aimchess\.com/public/quiz2?(?:[/?#]|$)", re.IGNORECASE
)

#: Where the puzzle array starts in its script.
_MARKER = "puzzles:[{difficulty:"
_PUZZLE = re.compile(
    r'\{difficulty:(\d+),category:"(\w+)".*?\bpgn:' + STRING, re.DOTALL
)


@dataclass(frozen=True)
class Puzzle:
    difficulty: int
    category: str
    pgn: str


def parse_puzzles(script: str) -> list[Puzzle]:
    start = script.find(_MARKER)
    matches = _PUZZLE.finditer(script, start) if start >= 0 else ()
    puzzles = [Puzzle(int(m.group(1)), m.group(2), string_value(m, 3)) for m in matches]
    if not puzzles:
        raise FetchError("found no puzzles in aimchess's quiz script")
    return puzzles


def category_to_pgn(category: str, puzzles: list[Puzzle]) -> str:
    """A category's puzzles as one PGN text, easiest first.

    ``Round`` is the difficulty, then the puzzle's place within it. The player
    is the side to move: the quiz asks for its best move.
    """
    event = f"{COLLECTION_NAME}: {category.title()}"
    places: Counter[int] = Counter()
    games = []
    for puzzle in sorted(puzzles, key=lambda p: p.difficulty):
        places[puzzle.difficulty] += 1
        round_ = f"{puzzle.difficulty}.{places[puzzle.difficulty]}"
        games.append(
            rewrite(
                puzzle.pgn,
                {"Event": event, "Site": SITE, "Round": round_},
                subject=f"aimchess quiz puzzle {category} {round_}",
                player=event,
                player_moves_first=True,
            )
        )
    return "\n\n".join(games)


class QuizExtractor(Extractor[dict[str, list[Puzzle]]]):
    """The quiz's puzzle set, one file per category."""

    key = "aimchess:quiz"
    url_patterns = (_URL,)

    def __init__(self, target: str) -> None:
        super().__init__()
        if _URL.match(target.strip()) is None:
            raise ValueError(f"not the aimchess quiz URL: {target!r}")

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], dict[str, list[Puzzle]]]:
        script = await find_script(fetcher, "/public/quiz", _MARKER)
        by_category: dict[str, list[Puzzle]] = {}
        for puzzle in parse_puzzles(script):
            by_category.setdefault(puzzle.category, []).append(puzzle)
        refs = [
            ItemRef(id=category, path=(f"{n:02d}-{slugify(category)}",))
            for n, category in enumerate(by_category, start=1)
        ]
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        return collection, refs, by_category

    async def _fetch(
        self, context: dict[str, list[Puzzle]], fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        return GameItem(pgn=category_to_pgn(ref.id, context[ref.id]))
