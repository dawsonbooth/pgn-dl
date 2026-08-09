"""Lesson payload decoding and PGN rewriting, against captured fixtures."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.core.registry import resolve_key, resolve_url
from pgn_dl.core.runner import Options, run
from pgn_dl.sources.chesscom.lessons import (
    BASE,
    LEARN_URL,
    LessonsExtractor,
    course_lesson_slugs,
    extract_learn_path,
    extract_lesson,
    learn_path_course,
    lesson_to_pgn,
    parse_target,
    question_to_pgn,
    translate_drawings,
)

FIX = Path(__file__).parents[2] / "fixtures" / "chesscom"

COURSE_URL = "https://www.chess.com/lessons/learn-to-play"
LESSON_URL = "https://www.chess.com/lessons/learn-to-play/the-king"
# In learn_path.json / learn_page.html this lesson course has no landing page.
LEARN_PATH_ONLY_COURSE_URL = "https://www.chess.com/lessons/chess-tactics"


def _payload(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_course_slugs_keep_curriculum_order_and_dedupe() -> None:
    html = (FIX / "course_page.html").read_text()
    slugs = course_lesson_slugs(html, "learn-to-play")
    # Document order, not alphabetical; duplicates collapsed; other lesson courses out.
    assert slugs == ["the-king", "the-queen", "the-rook", "castling"]


def _page_with(payload: dict) -> str:
    encoded = json.dumps(payload).replace("&", "&amp;").replace("'", "&#x27;")
    return f"<div data-lesson='{encoded}'></div>"


def test_extract_lesson_decodes_the_entity_encoded_attribute() -> None:
    assert (
        extract_lesson(_page_with(_payload("lesson_page.json")))["path"]
        == "in-between-moves"
    )


def test_extract_learn_path_decodes_the_uxxxx_escaped_blob() -> None:
    page = (FIX / "learn_page.html").read_text()
    payload = extract_learn_path(page)
    assert payload == _payload("learn_path.json")
    # Assert real decoded text survived the \uXXXX round trip (spaces, hyphens,
    # underscores all appear as escapes in the fixture), not just that some
    # dict came back.
    assert payload["sections"][0]["name"] == "New to Chess"
    first_lesson = payload["sections"][0]["courses"][0]["lessons"][0]
    assert first_lesson["name"] == "The King"
    assert first_lesson["path"] == "the-king"


def test_extract_learn_path_raises_fetch_error_when_the_blob_is_absent() -> None:
    with pytest.raises(FetchError):
        extract_learn_path("<html><body>no learn path here</body></html>")


def test_learn_path_course_returns_title_and_ordered_slugs_for_a_known_course() -> None:
    payload = _payload("learn_path.json")
    assert learn_path_course(payload, "chess-tactics") == (
        "Tactics",
        [
            "how-to-fork",
            "how-to-pin",
            "how-to-skewer",
            "trapped-pieces",
            "discoveries",
        ],
    )


def test_learn_path_course_returns_none_for_an_unknown_course() -> None:
    payload = _payload("learn_path.json")
    assert learn_path_course(payload, "no-such-course") is None


def test_learn_path_course_skips_lessons_with_no_path() -> None:
    # A lesson entry missing `path` names nothing to fetch, so it is skipped the
    # way a missing name is, rather than raising.
    payload = {
        "sections": [
            {
                "courses": [
                    {
                        "path": "some-lesson-course",
                        "name": "Some Lesson Course",
                        "lessons": [
                            {"name": "No Path Lesson"},
                            {"name": "Has Path Lesson", "path": "has-path"},
                        ],
                    }
                ]
            }
        ]
    }
    assert learn_path_course(payload, "some-lesson-course") == (
        "Some Lesson Course",
        ["has-path"],
    )


def test_learn_path_course_finds_advanced_chess_tactics() -> None:
    # The one lesson course with no landing page at all.
    payload = _payload("learn_path.json")
    assert learn_path_course(payload, "advanced-chess-tactics") == (
        "Advanced Tactics",
        [
            "remove-the-defender",
            "in-between-moves",
            "defense",
            "brilliant-sacrifices",
        ],
    )


def test_arrows_and_highlights_become_standard_commands() -> None:
    raw = (
        "[%c_arrow h6f8;color;rgba(255%2C%20170%2C%200%2C%200.8);opacity;0.8;"
        "from;h6;to;f8;persistent;true]"
        "[%c_highlight d2;color;%23ffaa00;opacity;0.8;square;d2;persistent;true,"
        "h6;color;%23ffaa00;opacity;0.8;square;h6;persistent;true] Some text."
    )
    out = translate_drawings(raw)
    assert "[%cal Yh6f8]" in out  # orange maps to yellow
    assert "[%csl Yd2,Yh6]" in out
    assert "c_arrow" not in out and "c_highlight" not in out
    assert "Some text." in out


def test_translate_drawings_recurses_into_variation_nodes() -> None:
    # The captured lesson's variation branches carry only plain-text comments,
    # so this question is synthetic: a mainline drawing, a variation drawing,
    # and one nested two levels deep inside that variation.
    question = (
        '[Event "?"]\n[Site "?"]\n[Date "????.??.??"]\n[Round "?"]\n'
        '[White "?"]\n[Black "?"]\n[Result "*"]\n\n'
        "1. e4 {[%c_arrow e2e4;color;rgba(255%2C%20170%2C%200%2C%200.8);opacity;0.8;"
        "from;e2;to;e4;persistent;true] Main line arrow.} "
        "e5 "
        "(1... c5 {[%c_highlight c5;color;%23ffaa00;opacity;0.8;square;c5;"
        "persistent;true] Variation highlight.} "
        "2. Nf3 (2. Nc3 {[%c_arrow b1c3;color;rgba(255%2C%20170%2C%200%2C%200.8);opacity;0.8;"
        "from;b1;to;c3;persistent;true] Nested variation arrow.}) "
        "Nc6) "
        "2. Nf3 *"
    )
    out = question_to_pgn(question, {"Event": "Test"})
    assert "[%cal Ye2e4]" in out  # mainline drawing
    assert "[%csl Yc5]" in out  # variation drawing (depth 1)
    assert "[%cal Yb1c3]" in out  # nested variation drawing (depth 2)
    assert "c_arrow" not in out and "c_highlight" not in out


def test_hint_and_wrong_commands_are_preserved() -> None:
    raw = "[%wrong Try again.][%hint Capture with check.] Well done."
    out = translate_drawings(raw)
    assert "[%wrong Try again.]" in out
    assert "[%hint Capture with check.]" in out


def test_question_rewrites_headers_and_stays_valid_pgn() -> None:
    payload = _payload("lesson_page.json")
    text = question_to_pgn(
        payload["questions"][0],
        {"Event": "Advanced Tactics", "Site": "chess.com", "Round": "1.1"},
    )
    game = chess.pgn.read_game(io.StringIO(text))
    assert game is not None
    assert game.headers["Event"] == "Advanced Tactics"
    assert game.headers["Round"] == "1.1"
    assert game.headers["FEN"].startswith("5k2/1p6")
    assert next(v.san() for v in game.variations) == "Bxh6+"


def test_capture_the_star_question_has_no_moves_but_still_exports() -> None:
    # A captureTheStar drill contains no moves at all. It must not be dropped
    # and must not raise.
    payload = _payload("lesson_page_star.json")
    text = question_to_pgn(payload["questions"][0], {"Event": "Learn To Play"})
    game = chess.pgn.read_game(io.StringIO(text))
    assert game is not None
    assert list(game.mainline_moves()) == []
    assert game.headers["LESSON_CHALLENGE_TYPE"] == "captureTheStar"
    assert game.headers["LESSON_STAR_LOCATIONS"] == "f2"
    assert text.rstrip().endswith("*")


def test_lesson_to_pgn_emits_one_game_per_question() -> None:
    payload = _payload("lesson_page.json")
    text = lesson_to_pgn(payload, {"Event": "Advanced Tactics", "Site": "chess.com"})
    stream = io.StringIO(text)
    games = []
    while (g := chess.pgn.read_game(stream)) is not None:
        games.append(g)
    assert len(games) == len(payload["questions"]) == 5
    assert all(g.headers["Event"] == "Advanced Tactics" for g in games)
    # Round numbers the question within the lesson.
    assert [g.headers["Round"] for g in games] == ["1", "2", "3", "4", "5"]


def test_lesson_to_pgn_records_the_lesson_identity() -> None:
    payload = _payload("lesson_page.json")
    text = lesson_to_pgn(payload, {})
    assert payload["id"] in text
    assert "In Between Moves" in text


def test_a_question_with_an_unplayable_move_raises_instead_of_truncating() -> None:
    # python-chess ignores the token it cannot read and plays on from there, so
    # this exports as "1. e4 e5 2. Nf3 Nc6 3. Bb5 *": Bb5 is silently reassigned
    # to White, and the game reads as a complete short one.
    question = (
        '[Event "?"]\n[Site "?"]\n[Date "????.??.??"]\n[Round "?"]\n'
        '[White "?"]\n[Black "?"]\n[Result "*"]\n\n'
        "1. e4 e5 2. Nf3 Nc6 3. Nf9 Bb5 4. O-O Nf6 *"
    )
    with pytest.raises(ValueError) as exc:
        question_to_pgn(question, {"Event": "Test"})
    assert "unreadable move" in str(exc.value)


def test_lesson_with_no_questions_raises_instead_of_exporting_nothing() -> None:
    # An empty export would be written as a 1-byte file, counted as written,
    # and then skipped by every later run because the file exists.
    payload = {"id": "abc", "name": "Empty", "path": "empty-lesson", "questions": []}
    with pytest.raises(ValueError) as exc:
        lesson_to_pgn(payload, {"Event": "Learn To Play"})
    assert "empty-lesson" in str(exc.value)


@pytest.mark.parametrize(
    "suffix",
    [
        "?ref=share",  # what chess.com's own share links produce
        "#challenge-2",
        "/?utm_source=x#top",
    ],
)
def test_query_and_fragment_do_not_defeat_the_end_anchored_pattern(suffix: str) -> None:
    assert parse_target(LESSON_URL + suffix) == ("learn-to-play", "the-king")
    # And the same URL still resolves through the bare-URL sugar, which
    # otherwise swallows the ValueError into "no source handles ...".
    assert isinstance(resolve_url(LESSON_URL + suffix), LessonsExtractor)


def test_a_courses_url_is_not_read_as_a_lesson_course() -> None:
    # With every prefix part optional this would parse as the lesson course
    # "courses", and the subcommand would GET /lessons/courses/....
    with pytest.raises(ValueError):
        parse_target("https://www.chess.com/courses/some-course")


def _learn_page_with(payload: dict) -> str:
    # Wrap payload the way chess.com's /learn page does: a JSON string body
    # assigned inside JSON.parse('...'). json.dumps of the JSON text produces
    # only standard backslash escapes (no single quote can appear, since JSON
    # keys/structure use double quotes), so this is a faithful-enough stand-in
    # for the real \uXXXX-escaped blob without needing to hand-encode one.
    raw = json.dumps(json.dumps(payload))[1:-1]
    return (
        "<script>window.chesscom.learnGuide.userLearnPath = "
        f"JSON.parse('{raw}');</script>"
    )


class FakeFetcher:
    """Serves the fixtures; 404s anything not explicitly registered."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.gets: list[str] = []

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:  # pragma: no cover
        raise AssertionError("lessons must never POST")

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.gets.append(url)
        try:
            return self.pages[url]
        except KeyError:
            raise FetchError(f"{url} failed: 404") from None


def _fetcher(*, course_page: bool = True) -> FakeFetcher:
    pages = {LESSON_URL: _page_with(_payload("lesson_page_star.json"))}
    if course_page:
        pages[COURSE_URL] = (FIX / "course_page.html").read_text()
    return FakeFetcher(pages)


def test_registered_and_resolvable_from_both_url_shapes() -> None:
    import pgn_dl.sources  # noqa: F401  - registers built-in sources

    assert resolve_key("chesscom:lessons") is LessonsExtractor
    assert isinstance(resolve_url(COURSE_URL), LessonsExtractor)
    assert isinstance(resolve_url(LESSON_URL), LessonsExtractor)


async def test_discover_lesson_course_lists_lessons_in_curriculum_order() -> None:
    collection, refs = await LessonsExtractor(COURSE_URL).discover(_fetcher())
    assert collection.source == "chesscom"
    assert collection.kind == "lessons"
    assert collection.slug == "learn-to-play"
    assert collection.name == "Learn To Play"
    assert [r.path for r in refs] == [
        ("001-the-king",),
        ("002-the-queen",),
        ("003-the-rook",),
        ("004-castling",),
    ]


async def test_discover_single_lesson_is_numbered_from_the_lesson_course() -> None:
    _, refs = await LessonsExtractor(LESSON_URL).discover(_fetcher())
    assert [r.path for r in refs] == [("001-the-king",)]


async def test_single_lesson_falls_back_to_unnumbered_without_a_course_page() -> None:
    # Numbering is impossible without the lesson-course page, and a whole
    # lesson-course download is impossible then too, so nothing can collide.
    f = _fetcher(course_page=False)
    ex = LessonsExtractor(LESSON_URL)
    collection, refs = await ex.discover(f)
    assert [r.path for r in refs] == [("the-king",)]
    # No listing supplied a title, so discovery names the collection by its
    # slug rather than paying for the lesson page to learn a better one.
    assert collection.name == "learn-to-play"
    # The real title rides in on the lesson page fetch() was going to make
    # anyway, and reaches the file that page produces.
    item = await ex.fetch(f, refs[0])
    assert item.pgn is not None
    assert '[Event "Learn To Play"]' in item.pgn


async def test_whole_lesson_course_without_a_course_page_fails_actionably() -> None:
    f = FakeFetcher({})
    with pytest.raises(FetchError) as exc:
        await LessonsExtractor(COURSE_URL).discover(f)
    assert "individual lesson" in str(exc.value).lower()


LOGIN_WALL = (
    '<html><head><meta property="og:title" content="Log In - Chess.com">'
    "</head><body>Log in to continue.</body></html>"
)


async def test_a_lesson_page_without_a_payload_fails_as_a_fetch_error() -> None:
    # The realistic trigger is an expired session: chess.com serves a 200 login
    # wall, so no HTTP error happens and only the missing payload gives it away.
    f = FakeFetcher({LESSON_URL: LOGIN_WALL})
    ex = LessonsExtractor(LESSON_URL)
    _, refs = await ex.discover(f)
    with pytest.raises(FetchError):
        await ex.fetch(f, refs[0])


async def test_a_login_wall_at_discovery_reaches_the_runners_error_path(
    tmp_path: Path,
) -> None:
    import pgn_dl.sources  # noqa: F401  - registers chesscom's login

    lines: list[str] = []
    report = await run(
        LessonsExtractor(COURSE_URL),
        FakeFetcher({COURSE_URL: LOGIN_WALL}),
        Options(out=tmp_path, delay=0.0),
        echo=lines.append,
    )
    # No traceback out of run(); driver.execute turns `stopped` into exit code 2.
    assert report.stopped
    assert any("pgn-dl login chesscom" in line for line in lines)


async def test_a_login_wall_on_a_single_lesson_is_tallied_as_a_failed_item(
    tmp_path: Path,
) -> None:
    # Discovery never requests the lesson, so an expired session surfaces one
    # phase later: as a failed item that says what happened and leaves nothing
    # on disk, rather than a run that reads as complete.
    lines: list[str] = []
    report = await run(
        LessonsExtractor(LESSON_URL),
        FakeFetcher({LESSON_URL: LOGIN_WALL}),
        Options(out=tmp_path, delay=0.0),
        echo=lines.append,
    )

    assert report.written == 0
    assert report.failed == 1
    assert not report.stopped
    assert any("session has expired" in line for line in lines)
    assert not (tmp_path / "chesscom/lessons/learn-to-play/the-king.pgn").exists()


async def test_a_lesson_course_page_listing_nothing_fails_actionably() -> None:
    # 200 but no lesson links (expired session, layout change). Reporting
    # "Log In - Chess.com - 0 items" and exiting 0 would be a silent success.
    f = FakeFetcher({COURSE_URL: LOGIN_WALL})
    with pytest.raises(FetchError) as exc:
        await LessonsExtractor(COURSE_URL).discover(f)
    assert "individual lesson" in str(exc.value).lower()


async def test_a_lesson_course_page_listing_nothing_supplies_no_title() -> None:
    f = FakeFetcher(
        {
            LESSON_URL: _page_with(_payload("lesson_page_star.json")),
            COURSE_URL: LOGIN_WALL,
        }
    )
    ex = LessonsExtractor(LESSON_URL)
    collection, refs = await ex.discover(f)
    assert [r.path for r in refs] == [("the-king",)]
    # "Log In - Chess.com" is the login page's og:title, not this lesson
    # course's name, so it is refused and the slug stands in until fetch()
    # reads the lesson's own course block.
    assert collection.name == "learn-to-play"
    item = await ex.fetch(f, refs[0])
    assert item.pgn is not None
    assert '[Event "Learn To Play"]' in item.pgn


async def test_a_learn_path_lesson_course_listing_no_lessons_fails_actionably() -> None:
    # The learn-path fallback answers the same way the landing-page branch
    # does: a lesson course resolved there with an empty lessons list is not a
    # silent zero-item success.
    payload = {
        "sections": [
            {
                "courses": [
                    {"path": "empty-lesson-course", "name": "Empty", "lessons": []},
                ]
            }
        ]
    }
    f = FakeFetcher({LEARN_URL: _learn_page_with(payload)})
    with pytest.raises(FetchError) as exc:
        await LessonsExtractor(
            "https://www.chess.com/lessons/empty-lesson-course"
        ).discover(f)
    assert "individual lesson" in str(exc.value).lower()


async def test_discover_falls_back_to_the_learn_path_without_a_landing_page() -> None:
    # chess-tactics has no /lessons/chess-tactics landing page (404), but it is
    # present in the /learn path blob.
    f = FakeFetcher({LEARN_URL: (FIX / "learn_page.html").read_text()})
    collection, refs = await LessonsExtractor(LEARN_PATH_ONLY_COURSE_URL).discover(f)
    assert collection.name == "Tactics"
    assert [r.path for r in refs] == [
        ("001-how-to-fork",),
        ("002-how-to-pin",),
        ("003-how-to-skewer",),
        ("004-trapped-pieces",),
        ("005-discoveries",),
    ]


async def test_advanced_chess_tactics_comes_from_the_learn_path() -> None:
    # advanced-chess-tactics has no landing page at all on chess.com, only a
    # learn-path entry. Pin the resume contract exactly.
    f = FakeFetcher({LEARN_URL: (FIX / "learn_page.html").read_text()})
    collection, refs = await LessonsExtractor(
        "https://www.chess.com/lessons/advanced-chess-tactics"
    ).discover(f)
    assert collection.name == "Advanced Tactics"
    assert collection.slug == "advanced-chess-tactics"
    assert [r.path for r in refs] == [
        ("001-remove-the-defender",),
        ("002-in-between-moves",),
        ("003-defense",),
        ("004-brilliant-sacrifices",),
    ]


async def test_discover_does_not_fetch_the_learn_path_when_a_landing_page_exists() -> (
    None
):
    f = _fetcher()  # learn-to-play has a landing page fixture registered
    await LessonsExtractor(COURSE_URL).discover(f)
    assert LEARN_URL not in f.gets


async def test_discover_fails_when_a_course_is_in_neither_listing() -> None:
    f = FakeFetcher({LEARN_URL: (FIX / "learn_page.html").read_text()})
    with pytest.raises(FetchError) as exc:
        await LessonsExtractor("https://www.chess.com/lessons/no-such-course").discover(
            f
        )
    msg = str(exc.value).lower()
    assert "individual lesson" in msg
    assert "landing page" in msg
    assert "learn path" in msg


async def test_fetch_returns_prerendered_pgn_for_the_whole_lesson() -> None:
    f = _fetcher()
    ex = LessonsExtractor(LESSON_URL)
    _, refs = await ex.discover(f)
    item = await ex.fetch(f, refs[0])
    assert item.pgn is not None
    assert item.pgn.count("[Event ") == 3  # three questions, three games
    assert "The King" in item.pgn
    assert "Learn To Play" in item.pgn


@pytest.mark.parametrize("target", [LESSON_URL, COURSE_URL])
async def test_discovery_requests_no_lesson_page(target: str) -> None:
    # The contract in core/extractor.py: discover() names every output path
    # without fetching any of them, so a re-run that finds the files already
    # there costs nothing.
    f = _fetcher()
    _, refs = await LessonsExtractor(target).discover(f)

    assert refs
    assert not any(g.startswith(f"{BASE}/learn-to-play/") for g in f.gets)


async def test_fetch_requests_the_lesson_page_exactly_once_and_never_posts() -> None:
    f = _fetcher()
    ex = LessonsExtractor(LESSON_URL)
    _, refs = await ex.discover(f)
    before = list(f.gets)
    await ex.fetch(f, refs[0])
    assert f.gets == [*before, LESSON_URL]


async def test_a_lesson_missing_from_the_listing_is_unnumbered() -> None:
    # A lesson course listing can resolve while still not naming a particular
    # lesson, since chess.com adds lessons over time. Within a run that cannot
    # collide: a whole-lesson-course download numbers only the lessons in
    # `slugs`, which by definition exclude this one. Both halves are pinned
    # here, the unnumbered fallback and the whole-course enumeration.
    en_passant_url = f"{BASE}/learn-to-play/en-passant"
    course_page = (FIX / "course_page.html").read_text()
    star = _page_with(_payload("lesson_page_star.json"))

    single = FakeFetcher({COURSE_URL: course_page, en_passant_url: star})
    _, single_refs = await LessonsExtractor(
        "https://www.chess.com/lessons/learn-to-play/en-passant"
    ).discover(single)
    assert [r.path for r in single_refs] == [("en-passant",)]

    whole = FakeFetcher({COURSE_URL: course_page})
    _, whole_refs = await LessonsExtractor(COURSE_URL).discover(whole)
    whole_paths = [r.path for r in whole_refs]
    assert ("en-passant",) not in whole_paths
    assert all("en-passant" not in p[0] for p in whole_paths)
    assert whole_paths == [
        ("001-the-king",),
        ("002-the-queen",),
        ("003-the-rook",),
        ("004-castling",),
    ]


async def test_a_whole_lesson_course_requests_each_lesson_page_once() -> None:
    star = _payload("lesson_page_star.json")
    pages = {COURSE_URL: (FIX / "course_page.html").read_text()}
    for slug in ("the-king", "the-queen", "the-rook", "castling"):
        pages[f"{BASE}/learn-to-play/{slug}"] = _page_with(star)
    f = FakeFetcher(pages)
    ex = LessonsExtractor(COURSE_URL)
    _, refs = await ex.discover(f)
    for ref in refs:
        await ex.fetch(f, ref)

    assert f.gets == [
        COURSE_URL,
        *(
            f"{BASE}/learn-to-play/{s}"
            for s in ("the-king", "the-queen", "the-rook", "castling")
        ),
    ]
