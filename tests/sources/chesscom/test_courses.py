"""The chess.com courses extractor: discovery, numbering, and PGN round-trip."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chess.pgn

from pgn_dl.core.models import Collection, GameItem
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.core.registry import resolve_key, resolve_url
from pgn_dl.sources.chesscom.courses import CoursesExtractor, course_id_from
from pgn_dl.sources.chesscom.parser import parse_lesson_tree

FIX = Path(__file__).parents[2] / "fixtures" / "chesscom"

COURSE_ID = "6445752b-f404-4d8f-9397-4fb03f690d8e"
COURSE_URL = f"https://www.chess.com/courses/train/{COURSE_ID}"

COLLECTION = Collection(
    source="chesscom",
    kind="courses",
    slug="test-course",
    name="Test Course",
    headers={"Event": "Test Course", "Site": "chess.com", "Annotator": "GM Tester"},
)


def _load(fixture: str) -> Any:
    return json.loads((FIX / fixture).read_text())


def _pgn(fixture: str, round_: str) -> str:
    content = parse_lesson_tree(_load(fixture))
    item = GameItem(
        headers={"Round": round_, "ChessComLessonId": content.id},
        starting_fen=content.starting_fen,
        comment=content.description,
        root=content.root,
    )
    return game_to_pgn(COLLECTION, item)


def test_intro_lesson_emits_pgn_with_description_and_no_moves() -> None:
    pgn = _pgn("lesson_intro.json", "1.1")
    assert '[Event "Test Course"]' in pgn
    assert '[Round "1.1"]' in pgn
    assert "Aman Hambleton" in pgn  # description body
    assert pgn.rstrip().endswith("*")  # no moves, just the result marker


def test_real_game_round_trips_via_python_chess() -> None:
    reparsed = chess.pgn.read_game(io.StringIO(_pgn("lesson_real_game.json", "1.3")))
    assert reparsed is not None
    assert reparsed.headers["Event"] == "Test Course"

    mainline = list(reparsed.mainline_moves())
    board = reparsed.board()
    sans = []
    for m in mainline[:8]:
        sans.append(board.san(m))
        board.push(m)
    assert sans == ["d4", "d5", "Bf4", "Nf6", "e3", "e6", "Nd2", "c5"]

    # At move 9 the mainline is e4; the lesson annotates an Ne5 alternative,
    # which must survive as a sibling variation through export and re-parse.
    node = reparsed
    for _ in range(16):  # 8 full moves = 16 plies -> the 8...b6 node
        node = node.variations[0]
    child_sans = [v.san() for v in node.variations]
    assert child_sans[0] == "e4"  # mainline continuation stays first
    assert "Ne5" in child_sans[1:]  # the annotated alternative is a sibling


def test_course_id_accepts_url_or_bare_uuid() -> None:
    assert course_id_from(COURSE_ID) == COURSE_ID
    assert course_id_from(COURSE_URL) == COURSE_ID


class _FakeFetcher:
    """A :class:`~pgn_dl.core.fetch.Fetcher` replaying captured RPC responses.

    The RPC URL ends in the method name, so dispatching on the suffix is enough
    to stand in for the whole courses service.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        self.calls.append(url)
        if url.endswith("AuthenticateForCoursesWithCookie"):
            # The token exchange rides on the cookie, so it must not be Bearer-authed.
            assert "authorization" not in headers
            return {"accessToken": "test-jwt"}
        assert headers["authorization"] == "Bearer test-jwt"
        if url.endswith("GetCourseLearningSession"):
            assert json["contextId"] == COURSE_ID
            return _load("learning_session.json")
        raise AssertionError(f"unexpected RPC call: {url}")

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        raise AssertionError("the courses extractor never issues GETs")


async def test_discover_builds_the_collection_from_the_course_outline() -> None:
    fetcher = _FakeFetcher()
    collection, refs = await CoursesExtractor(COURSE_URL).discover(fetcher)

    assert collection.source == "chesscom"
    assert collection.kind == "courses"
    assert collection.slug == "aman-hambleton-s-london-system-353577"
    assert collection.name == "Aman Hambleton's London System"
    assert collection.headers["Event"] == "Aman Hambleton's London System"
    assert collection.headers["Site"] == "chess.com"
    assert collection.headers["Annotator"] == "Aman Hambleton"

    assert len(refs) == 866
    # Authenticate first, then the outline: one call each, no extra traffic.
    assert len(fetcher.calls) == 2


async def test_discover_numbers_item_paths_for_resume() -> None:
    # These paths are the resume contract: a re-run skips an item when its file
    # already exists, so any drift here silently re-downloads or skips the wrong
    # lessons. Pin the literal strings rather than the shape.
    _, refs = await CoursesExtractor(COURSE_URL).discover(_FakeFetcher())

    assert refs[0].path == ("01-introduction", "001-welcome-to-the-london")
    assert refs[1].path == ("01-introduction", "002-tips-on-studying-this-course")
    assert refs[2].path == (
        "01-introduction",
        "003-jorden-van-foreest-rasmus-svane-bundesliga-2016-17",
    )


async def test_lesson_numbering_restarts_in_each_chapter() -> None:
    _, refs = await CoursesExtractor(COURSE_URL).discover(_FakeFetcher())

    # Chapter 1 holds exactly three lessons, so refs[3] opens chapter 2 and its
    # lesson counter must be back to 001 rather than continuing at 004.
    assert refs[3].path == ("02-move-orders", "001-move-orders-1-nf6-2-c5")
    # Chapters are zero-padded to two digits and lessons to three, independently,
    # all the way out to the last chapter.
    assert refs[-1].path == (
        "23-reference-games",
        "048-mchedlishvili-mikheil-vs-caruana-fabiano-baku-2023",
    )


class _FetcherMissingAChapter(_FakeFetcher):
    """The outline with its first chapter absent, its lessons still listed.

    Every lesson in the captured course names a chapter the outline lists, so
    this shape has to be built rather than captured.
    """

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        raw = await super().post_json(url, headers=headers, json=json)
        if url.endswith("GetCourseLearningSession"):
            trainer = raw["learningSession"]["moveTrainers"][0]
            trainer["chapters"] = trainer["chapters"][1:]
        return raw


async def test_lessons_outside_the_listed_chapters_are_still_downloaded() -> None:
    # Dropped, they would leave 41 lessons missing from a run that reported
    # total=825 written=825 failed=0.
    _, refs = await CoursesExtractor(COURSE_URL).discover(_FetcherMissingAChapter())

    assert len(refs) == 866
    unlisted = [r for r in refs if r.path[0] == "23-unlisted"]
    assert [r.path[1] for r in unlisted] == [
        "001-welcome-to-the-london",
        "002-tips-on-studying-this-course",
        "003-jorden-van-foreest-rasmus-svane-bundesliga-2016-17",
    ]
    # The chapters that are listed keep the numbering they always had.
    assert refs[3].path == ("01-move-orders", "001-move-orders-1-nf6-2-c5")


REAL_GAME_ID = "18573b71-1751-4a5e-8509-9db4500a145e"  # lesson_real_game.json


class _LessonFetcher(_FakeFetcher):
    """The outline plus the lesson RPC, replaying a captured lesson response.

    ``lessons`` overrides the response for a particular ``variationId``; every
    other id gets the real-game fixture, which is enough for the paths that do
    not care which lesson came back.
    """

    def __init__(self, lessons: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.lessons = lessons or {}
        self.variation_ids: list[str] = []

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        if url.endswith("GetCourseTrainingForLearning"):
            self.calls.append(url)
            assert headers["authorization"] == "Bearer test-jwt"
            self.variation_ids.append(json["variationId"])
            return self.lessons.get(json["variationId"], _load("lesson_real_game.json"))
        return await super().post_json(url, headers=headers, json=json)


async def test_fetch_requests_the_refs_own_lesson_and_records_its_identity() -> None:
    fetcher = _LessonFetcher()
    extractor = CoursesExtractor(COURSE_URL)
    _, refs = await extractor.discover(fetcher)

    item = await extractor.fetch(fetcher, refs[2])

    # The ref's id is what addresses the lesson, and the id written into the
    # PGN is the one the response came back carrying, not the one asked for.
    assert fetcher.variation_ids == [refs[2].id]
    assert item.headers["ChessComLessonId"] == REAL_GAME_ID
    # A reader keys on Round to put a lesson back in course order.
    assert item.headers["Round"] == "1.3"
    assert [c.san for c in item.root.children][:1] == ["d4"]


async def test_fetch_numbers_each_lesson_by_its_chapter_and_position() -> None:
    # refs[3] opens chapter 2, so its Round restarts at 2.1 rather than running
    # on from the previous chapter.
    fetcher = _LessonFetcher()
    extractor = CoursesExtractor(COURSE_URL)
    _, refs = await extractor.discover(fetcher)

    assert (await extractor.fetch(fetcher, refs[0])).headers["Round"] == "1.1"
    assert (await extractor.fetch(fetcher, refs[3])).headers["Round"] == "2.1"


async def test_fetch_keeps_a_lesson_that_starts_from_its_own_position() -> None:
    # Both committed lesson fixtures open from the standard position, so a
    # fetch that ignored startingPosition would look right against either.
    fen = "r1bqkb1r/pp2pppp/2n2n2/3p4/3P4/2PB4/PP3PPP/RNBQK1NR w KQkq - 1 6"
    raw = _load("lesson_real_game.json")
    raw["lesson"]["variation"]["startingPosition"]["fen"] = fen
    raw["lesson"]["plies"] = [{"san": "Nf3"}]

    fetcher = _LessonFetcher()
    extractor = CoursesExtractor(COURSE_URL)
    _, refs = await extractor.discover(fetcher)
    fetcher.lessons[refs[0].id] = raw

    item = await extractor.fetch(fetcher, refs[0])

    assert item.starting_fen == fen
    assert f'[FEN "{fen}"]' in game_to_pgn(COLLECTION, item)


def test_the_extractor_points_at_the_chesscom_browser_profile() -> None:
    # The driver asks the extractor where its session lives, so this is the only
    # link between a browser-backed source and its own profile directory.
    from pgn_dl.sources.chesscom.auth import profile_dir

    assert CoursesExtractor(COURSE_ID).browser_profile() == profile_dir()


def test_importing_sources_registers_the_extractor() -> None:
    # Importing the package is the registration; without it the CLI resolves
    # nothing.
    import pgn_dl.sources  # noqa: F401

    assert resolve_key("chesscom:courses") is CoursesExtractor

    got = resolve_url(COURSE_URL)
    assert isinstance(got, CoursesExtractor)
    assert got.course_id == COURSE_ID
