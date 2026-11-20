"""The chess.com opening-courses extractor: targets, the service it calls, output."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.core.registry import UnknownSource, resolve_url
from pgn_dl.sources.chesscom.opening_courses import OpeningCoursesExtractor, slug_from

FIX = Path(__file__).parents[2] / "fixtures" / "chesscom"

SLUG = "caro-kann-defense-396427"
COURSE_URL = f"https://www.chess.com/openings/courses/{SLUG}"
COURSE_ID = "1493cca3-e6ba-4ece-9c39-051bbf1867b2"  # the product's defaultMoveTrainerId
FIRST_LESSON_ID = "6c6bc637-d62c-4e22-93e5-7c17ee5458f8"

SERVICE = "https://www.chess.com/service/opening-courses/rpc/"


def _load(fixture: str) -> Any:
    return json.loads((FIX / fixture).read_text(encoding="utf-8"))


def test_slug_from_reads_a_course_url_or_a_bare_slug() -> None:
    assert slug_from(COURSE_URL) == SLUG
    assert slug_from(SLUG) == SLUG
    assert slug_from(SLUG.upper()) == SLUG
    # Share links carry a query and in-page links a fragment.
    assert slug_from(f"{COURSE_URL}/?ref=share#lines") == SLUG


@pytest.mark.parametrize(
    "target",
    [
        "",
        # The opening courses home page lists courses; it is not one.
        "https://www.chess.com/openings/courses",
        # A Courses course, and an opening explorer page.
        "https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e",
        "https://www.chess.com/openings/Caro-Kann-Defense",
        f"https://example.com/openings/courses/{SLUG}",
    ],
)
def test_slug_from_refuses_a_target_that_names_no_opening_course(target: str) -> None:
    with pytest.raises(ValueError):
        slug_from(target)


def test_an_opening_course_url_resolves_to_the_opening_courses_extractor() -> None:
    extractor = resolve_url(COURSE_URL)
    assert isinstance(extractor, OpeningCoursesExtractor)
    assert extractor.slug == SLUG


def test_the_opening_courses_home_page_is_not_downloadable() -> None:
    with pytest.raises(UnknownSource):
        resolve_url("https://www.chess.com/openings/courses")


class _OpeningCoursesService:
    """A fake of the opening-courses deployment.

    Any call to another deployment fails the test: the Courses deployment would
    answer it with a truncated preview, not an error.
    """

    def __init__(self, product: dict[str, Any] | None = None) -> None:
        self.product = (
            product if product is not None else _load("opening_course_product.json")
        )
        self.calls: list[str] = []
        self.issued: list[str] = []
        self.expired: set[str] = set()

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        assert url.startswith(SERVICE), f"called outside opening courses: {url}"
        method = url.rsplit("/", 1)[1]
        self.calls.append(method)
        if method == "AuthenticateForCoursesWithCookie":
            assert "authorization" not in headers
            self.issued.append(f"jwt-{len(self.issued) + 1}")
            return {"accessToken": self.issued[-1]}
        token = headers["authorization"].removeprefix("Bearer ")
        if token in self.expired:
            raise FetchError(f"{url} failed: 401 (JSON body)", status=401)
        if method == "GetCourseProductBySlug":
            if json["slug"] != SLUG:
                raise FetchError(f"{url} failed: 404 (JSON body)", status=404)
            return self.product
        if method == "GetCourseLearningSession":
            assert json["contextId"] == COURSE_ID
            return _load("opening_course_session.json")
        if method == "GetCourseTrainingForLearning":
            return _load("opening_course_lesson.json")
        raise AssertionError(f"unexpected RPC call: {url}")

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        raise AssertionError("the opening-courses extractor never issues GETs")


async def test_discover_opens_the_course_the_slug_names() -> None:
    service = _OpeningCoursesService()
    collection, refs = await OpeningCoursesExtractor(COURSE_URL).discover(service)

    assert service.calls == [
        "AuthenticateForCoursesWithCookie",
        "GetCourseProductBySlug",
        "GetCourseLearningSession",
    ]
    assert collection.source == "chesscom"
    assert collection.kind == "opening-courses"
    assert collection.slug == SLUG
    assert len(refs) == 8
    assert refs[0].id == FIRST_LESSON_ID


async def test_discover_numbers_item_paths_for_resume() -> None:
    _, refs = await OpeningCoursesExtractor(SLUG).discover(_OpeningCoursesService())

    assert refs[0].path == ("01-caro-kann-defense", "001-exchange-variation-1")
    assert refs[-1].path == (
        "01-caro-kann-defense",
        "008-classical-variation-rook-pawn-attack",
    )


async def test_the_course_is_named_with_the_side_it_teaches() -> None:
    # Both Caro-Kann courses are named "Caro-Kann Defense"; the side tells them apart.
    collection, _ = await OpeningCoursesExtractor(SLUG).discover(
        _OpeningCoursesService()
    )

    assert collection.name == "Caro-Kann Defense (Black)"
    assert collection.headers == {
        "Event": "Caro-Kann Defense (Black)",
        "Site": "chess.com",
        "Annotator": "Chess.com",
    }


async def test_a_course_for_white_is_named_for_white() -> None:
    product = _load("opening_course_product.json")
    product["details"]["product"]["color"] = ["COLOR_WHITE"]

    collection, _ = await OpeningCoursesExtractor(SLUG).discover(
        _OpeningCoursesService(product)
    )

    assert collection.headers["Event"] == "Caro-Kann Defense (White)"


_ABSENT = object()


@pytest.mark.parametrize(
    "color",
    [_ABSENT, None, [], ["COLOR_WHITE", "COLOR_BLACK"]],
    ids=["absent", "null", "empty", "both-sides"],
)
async def test_a_course_naming_no_single_side_keeps_its_own_name(
    color: object,
) -> None:
    product = _load("opening_course_product.json")
    if color is _ABSENT:
        del product["details"]["product"]["color"]
    else:
        product["details"]["product"]["color"] = color

    collection, _ = await OpeningCoursesExtractor(SLUG).discover(
        _OpeningCoursesService(product)
    )

    assert collection.headers["Event"] == "Caro-Kann Defense"


async def test_an_unknown_slug_fails_naming_the_slug() -> None:
    with pytest.raises(FetchError) as exc:
        await OpeningCoursesExtractor("no-such-course-1").discover(
            _OpeningCoursesService()
        )

    assert exc.value.status == 404
    assert "no-such-course-1" in str(exc.value)


async def test_fetch_reads_the_lesson_from_the_opening_courses_service() -> None:
    service = _OpeningCoursesService()
    extractor = OpeningCoursesExtractor(SLUG)
    collection, refs = await extractor.discover(service)

    item = await extractor.fetch(service, refs[0])

    assert service.calls[-1] == "GetCourseTrainingForLearning"
    assert item.headers == {"ChessComLessonId": FIRST_LESSON_ID, "Round": "1.1"}
    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    assert game.headers["Event"] == "Caro-Kann Defense (Black)"
    sans = [node.san() for node in game.mainline()]
    assert sans[:4] == ["e4", "c6", "d4", "d5"]
    assert sans[-2:] == ["O-O", "Be7"]
    assert len(sans) == 14
    first = game.next()
    assert first is not None
    assert first.comment.startswith("The most popular way to open the game")


async def test_a_lesson_after_the_token_expires_is_fetched_with_a_new_one() -> None:
    # The token renewal must go to the opening-courses deployment too.
    service = _OpeningCoursesService()
    extractor = OpeningCoursesExtractor(SLUG)
    _, refs = await extractor.discover(service)
    service.expired.update(service.issued)

    item = await extractor.fetch(service, refs[0])

    assert item.headers["ChessComLessonId"] == FIRST_LESSON_ID
    assert service.issued == ["jwt-1", "jwt-2"]
