"""The quiz: target, puzzles from the script, one file per category."""

from __future__ import annotations

import io

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.aimchess.quiz import QuizExtractor, parse_puzzles

from .conftest import QUIZ_CHUNK, SiteFetcher


def test_the_quiz_urls_resolve_to_this_extractor() -> None:
    assert isinstance(
        registry.resolve_url("https://aimchess.com/public/quiz"), QuizExtractor
    )
    assert isinstance(
        registry.resolve_url("https://aimchess.com/public/quiz2"), QuizExtractor
    )
    assert QuizExtractor.from_url("https://aimchess.com/public/quizzes") is None


def test_puzzles_are_read_with_their_pgn_decoded() -> None:
    puzzles = parse_puzzles(QUIZ_CHUNK)
    assert [(p.difficulty, p.category) for p in puzzles] == [
        (2, "opening"),
        (1, "opening"),
        (1, "tactics"),
    ]
    assert "don't block" in puzzles[0].pgn and "\n" in puzzles[0].pgn
    with pytest.raises(FetchError):
        parse_puzzles("var i={year:2021,month:9,puzzles:[]};")


async def test_each_category_is_a_file_of_games_easiest_first() -> None:
    extractor = QuizExtractor("https://aimchess.com/public/quiz")
    fetcher = SiteFetcher()
    collection, refs = await extractor.discover(fetcher)
    assert (collection.kind, collection.slug) == ("quiz", "aimchess-quiz")
    assert [r.path for r in refs] == [("01-opening",), ("02-tactics",)]
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn is not None
    buf = io.StringIO(item.pgn)
    games = []
    while (game := chess.pgn.read_game(buf)) is not None:
        games.append(game)
    assert [g.headers["Round"] for g in games] == ["1.1", "2.1"]
    first = games[0]
    assert first.headers["Event"] == "Aimchess Quiz: Opening"
    # The player is the side to move, whom the quiz asks for the best move.
    assert (first.headers["White"], first.headers["Black"]) == (
        "?",
        "Aimchess Quiz: Opening",
    )
    best = first.next()
    assert best is not None and best.san() == "Nc6"
    assert best.comment == "Develop, and don't block the d-pawn."
    assert best.parent.variations[1].comment == "This bishop blocks the d7 pawn."
