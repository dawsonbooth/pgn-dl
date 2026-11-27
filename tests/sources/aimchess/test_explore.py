"""Explore: targets, the listing, layout, and downloading lessons."""

from __future__ import annotations

import io

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import Blocked, FetchError, UnknownTarget
from pgn_dl.sources.aimchess.client import Client, DailyLimitReached
from pgn_dl.sources.aimchess.explore import (
    ExploreExtractor,
    layout,
    lesson_id_from,
    parse_listing,
    parse_steps,
)

from .conftest import (
    LESSONS,
    LISTING_URL,
    TOKEN,
    FakeFetcher,
    entry,
    lesson_body,
    lesson_url,
    listing_body,
)

pytestmark = pytest.mark.usefixtures("stub_token")


@pytest.mark.parametrize(
    "target",
    [
        "https://aimchess.com/explore",
        "https://aimchess.com/explore/",
        "https://www.aimchess.com/explore?tab=1",
    ],
)
def test_explore_s_own_url_is_refused(target: str) -> None:
    with pytest.raises(
        WholeSection, match="is all of Explore; give one lesson's URL or id"
    ):
        lesson_id_from(target)


def test_the_registry_refuses_explore_s_own_url() -> None:
    with pytest.raises(WholeSection, match="is all of Explore"):
        registry.resolve_url("https://aimchess.com/explore")


@pytest.mark.parametrize(
    "target",
    [
        "https://aimchess.com/explore/24",
        "https://aimchess.com/explore/24/analysis",
        "https://aimchess.com/explore/24?ref=share",
        "24",
        "024",
    ],
)
def test_a_lesson_url_or_id_means_that_lesson(target: str) -> None:
    assert lesson_id_from(target) == "24"


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/explore/24",
        "https://aimchess.com/explore/abc",
        "https://aimchess.com/explorer",
        "",
    ],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(
        ValueError, match="could not read an aimchess Explore"
    ) as refused:
        lesson_id_from(target)
    assert not isinstance(refused.value, WholeSection)


def test_explore_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url("https://aimchess.com/explore/24")
    assert isinstance(found, ExploreExtractor) and found.lesson_id == "24"
    assert ExploreExtractor.from_url("https://aimchess.com/explorer") is None


def test_parse_listing() -> None:
    topics, lessons = parse_listing(listing_body())
    assert topics == ["Endgame", "Middlegame", "Opening"]
    assert [x.id for x in lessons] == ["24", "1", "17", "2", "40"]
    assert lessons[0].section == "Strategic concepts"
    assert lessons[-1].section == ""  # null upstream


@pytest.mark.parametrize(
    "listing",
    [
        {"lessons": [{"id": 3}]},
        {"topics": [], "lessons": [{"id": 3}]},
        {"topics": ["Endgame"], "lessons": [{"id": 3}]},
        {"topics": [{"name": "Endgame"}], "lessons": [{"id": "3"}]},
    ],
)
def test_an_unreadable_listing_is_refused(listing: dict) -> None:
    # The topics number the directories and the ids address the lessons;
    # guessing either could move every file on a later run.
    with pytest.raises(FetchError):
        parse_listing(listing)


def test_parse_steps() -> None:
    steps = parse_steps(lesson_body(LESSONS[0]), "24")
    assert [s.type for s in steps] == ["tutorial", "exercise"]
    with pytest.raises(FetchError, match="no steps"):
        parse_steps({"steps_data": []}, "24")
    with pytest.raises(FetchError, match="step 2 has no PGN"):
        parse_steps({"steps_data": [{"pgn": "1. e4 *"}, {"pgn": ""}]}, "24")


def _paths(lessons: tuple[dict, ...]) -> list[tuple[str, tuple[str, str]]]:
    return [(x.id, path) for x, path in layout(*parse_listing(listing_body(lessons)))]


def test_lessons_are_numbered_within_topics_in_listing_order() -> None:
    assert _paths(LESSONS) == [
        ("24", ("02-middlegame", "001-doubled-pawns")),
        ("1", ("01-endgame", "001-rook-endings")),
        ("17", ("02-middlegame", "002-greek-gift")),
        ("2", ("01-endgame", "002-queen-endings")),
        ("40", ("03-opening", "001-center-control")),
    ]


def test_a_coming_soon_lesson_is_skipped_but_keeps_its_number() -> None:
    lessons = (
        entry(1, "Rook Endings", "Endgame"),
        entry(5, "Pawn Endings", "Endgame", status="coming soon"),
        entry(2, "Queen Endings", "Endgame"),
    )
    assert _paths(lessons) == [
        ("1", ("01-endgame", "001-rook-endings")),
        ("2", ("01-endgame", "003-queen-endings")),
    ]


def test_a_topic_missing_from_the_tabs_comes_after_them() -> None:
    lessons = (entry(9, "Idea", "New Topic"), entry(1, "Rook Endings", "Endgame"))
    assert _paths(lessons) == [
        ("9", ("04-new-topic", "001-idea")),
        ("1", ("01-endgame", "001-rook-endings")),
    ]


async def test_discovery_reads_only_the_listing() -> None:
    fetcher = FakeFetcher()
    collection, _ = await ExploreExtractor("17").discover(fetcher)
    assert (collection.source, collection.kind, collection.slug) == (
        "aimchess",
        "explore",
        "explore-chess",
    )
    assert (fetcher.gets, fetcher.posts) == ([LISTING_URL], [])


async def test_a_lesson_gets_the_path_the_listing_gives_it() -> None:
    _, refs = await ExploreExtractor("17").discover(FakeFetcher())
    assert [(r.id, r.path) for r in refs] == [
        (lesson, path) for lesson, path in _paths(LESSONS) if lesson == "17"
    ]


async def test_a_listing_without_lessons_means_the_site_changed() -> None:
    fetcher = FakeFetcher(listing=listing_body(lessons=()))
    with pytest.raises(FetchError, match="has no lessons") as refused:
        await ExploreExtractor("17").discover(fetcher)
    assert not isinstance(refused.value, UnknownTarget)


async def test_an_unlisted_lesson_is_refused() -> None:
    with pytest.raises(UnknownTarget, match="lesson 99 is not a published"):
        await ExploreExtractor("99").discover(FakeFetcher())


def _games(text: str) -> list[chess.pgn.Game]:
    buf = io.StringIO(text)
    games = []
    while (game := chess.pgn.read_game(buf)) is not None:
        assert not game.errors
        games.append(game)
    return games


async def _fetch(lesson_id: str, fetcher: FakeFetcher) -> str:
    extractor = ExploreExtractor(lesson_id)
    _, [ref] = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, ref)
    assert item.pgn is not None
    return item.pgn


async def test_a_lesson_is_a_file_with_a_game_per_step() -> None:
    fetcher = FakeFetcher(lessons={"24": lesson_body(LESSONS[0])})
    tutorial, exercise = _games(await _fetch("24", fetcher))
    assert fetcher.posts == [lesson_url(24)]
    assert tutorial.headers["Event"] == "Doubled Pawns"
    assert [g.headers["Round"] for g in (tutorial, exercise)] == ["1", "2"]
    assert [g.headers["AimchessStepType"] for g in (tutorial, exercise)] == [
        "tutorial",
        "exercise",
    ]
    assert tutorial.headers["AimchessSection"] == "Strategic concepts"
    # The player is the side that replies to the first move.
    assert (tutorial.headers["White"], tutorial.headers["Black"]) == (
        "?",
        "Doubled Pawns",
    )
    assert (exercise.headers["White"], exercise.headers["Black"]) == (
        "Doubled Pawns",
        "?",
    )


async def test_a_lesson_without_a_section_gets_no_section_header() -> None:
    games = _games(
        await _fetch("40", FakeFetcher(lessons={"40": lesson_body(LESSONS[4])}))
    )
    assert all("AimchessSection" not in g.headers for g in games)


async def test_a_lesson_refused_for_the_daily_limit_says_so() -> None:
    fetcher = FakeFetcher(statuses={"1": 403})
    with pytest.raises(DailyLimitReached, match="daily Explore limit"):
        await _fetch("1", fetcher)


async def test_other_refusals_pass_through() -> None:
    with pytest.raises(FetchError) as exc:
        await _fetch("24", FakeFetcher(statuses={"24": 500}))
    assert exc.value.status == 500 and "daily" not in str(exc.value)


async def test_a_bot_challenge_is_not_read_as_the_daily_limit() -> None:
    class Challenged(FakeFetcher):
        async def post_json(self, url, *, headers, json):
            raise Blocked(f"{url}: bot challenge (status 403)", status=403)

    with pytest.raises(Blocked):
        await Client(Challenged(), token=TOKEN).explore_lesson("24")


@pytest.mark.parametrize("body", ["<html>sign in</html>", "[]"])
async def test_a_listing_that_is_not_a_json_object_fails(body: str) -> None:
    class Raw(FakeFetcher):
        async def get_text(self, url, *, headers):
            return body

    with pytest.raises(FetchError) as exc:
        await Client(Raw(), token=TOKEN).explore_listing()
    assert "sign in" not in str(exc.value) and TOKEN not in str(exc.value)
