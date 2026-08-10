"""CoursesExtractor: id parsing, URL claiming, discovery, and fetch."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import chess
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessable import auth
from pgn_dl.sources.chessable.courses import CoursesExtractor, course_id_from


@pytest.fixture(autouse=True)
def _profile(tmp_path, monkeypatch):
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_credentials(jwt="DUMMY-JWT", uid="424242")


def test_course_id_from_reads_a_bare_number() -> None:
    assert course_id_from("277570") == "277570"


def test_course_id_from_reads_the_several_url_shapes() -> None:
    assert course_id_from("https://www.chessable.com/course/277570/") == "277570"
    assert (
        course_id_from("https://www.chessable.com/mastering-time/course/277570/")
        == "277570"
    )
    assert course_id_from("https://www.chessable.com/learn/25098") == "25098"


def test_course_id_from_rejects_a_non_course_target() -> None:
    with pytest.raises(ValueError):
        course_id_from("https://www.chessable.com/blog/some-post")


def test_from_url_claims_course_urls_and_declines_others() -> None:
    assert (
        CoursesExtractor.from_url("https://www.chessable.com/x/course/277570/")
        is not None
    )
    assert CoursesExtractor.from_url("https://www.chessable.com/blog/post") is None
    assert CoursesExtractor.from_url("https://example.com/course/1/") is None


class FakeFetcher:
    """Answers getCourse vs getGame by URL substring."""

    def __init__(self, course: dict, games: dict[str, dict]) -> None:
        self._course = course
        self._games = games

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        if "/getCourse" in url:
            return json.dumps(self._course)
        for oid, body in self._games.items():
            if f"oid={oid}" in url:
                return json.dumps(body)
        raise AssertionError(f"unexpected url {url}")

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        raise AssertionError("chessable only issues GETs")


def _course_body(*, owned: bool = True) -> dict:
    return {
        "course": {
            "owned": owned,
            "name": "Mastering Time",
            "isAuthor": False,
            "data": [
                {
                    "id": 0,
                    "title": "Introduction",
                    "variations": [
                        {"oid": 100, "name": "Welcome", "type": "informational"}
                    ],
                },
                {
                    "id": 1,
                    "title": "Level 1",
                    "variations": [
                        {"oid": 101, "name": "Game #1", "type": "puzzles"},
                        {"oid": 102, "name": "Game #2", "type": "puzzles"},
                    ],
                },
            ],
        }
    }


def _game_body(san: str, *, oid: int) -> dict:
    def blob(data):
        return json.dumps({"before": "f", "after": "f", "data": data})

    return {
        "game": {
            "owned": True,
            "data": [
                {
                    "id": 0,
                    "mid": 0,
                    "move": 1,
                    "col": "w",
                    "san": san,
                    "isKey": True,
                    "ann1": "",
                    "ann2": "",
                    "draws": [],
                    "before": blob([]),
                    "after": blob([]),
                }
            ],
            "initial": chess.STARTING_FEN,
            "isInfo": 0,
            "isAlt": 0,
            "result": "*",
            "oid": oid,
        }
    }


async def test_discover_numbers_chapters_and_variations() -> None:
    extractor = CoursesExtractor("277570")
    collection, refs = await extractor.discover(FakeFetcher(_course_body(), {}))
    assert collection.name == "Mastering Time"
    assert collection.source == "chessable" and collection.kind == "courses"
    assert collection.headers["Site"] == "chessable.com"
    paths = [r.path for r in refs]
    assert paths == [
        ("01-introduction", "001-welcome"),
        ("02-level-1", "001-game-1"),
        ("02-level-1", "002-game-2"),
    ]
    assert [r.id for r in refs] == ["100", "101", "102"]


async def test_discover_refuses_a_course_the_account_does_not_own() -> None:
    extractor = CoursesExtractor("277570")
    with pytest.raises(FetchError):
        await extractor.discover(FakeFetcher(_course_body(owned=False), {}))


async def test_fetch_builds_a_game_item_with_round_and_id_headers() -> None:
    games = {"101": _game_body("e4", oid=101)}
    extractor = CoursesExtractor("277570")
    _, refs = await extractor.discover(FakeFetcher(_course_body(), games))
    game_ref = next(r for r in refs if r.id == "101")
    item = await extractor.fetch(FakeFetcher(_course_body(), games), game_ref)
    assert item.headers["ChessableVariationId"] == "101"
    assert item.headers["Round"] == "2.1"  # chapter 2, variation 1
    assert item.root.children[0].san == "e4"
