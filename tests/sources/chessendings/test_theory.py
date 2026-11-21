"""Theory: targets, the listing, layout, guests and members, and Pro lessons."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.core.runner import Options, run
from pgn_dl.sources.chessendings import auth, flight
from pgn_dl.sources.chessendings.client import Client
from pgn_dl.sources.chessendings.pages import ProRequired, SignedOut
from pgn_dl.sources.chessendings.section import Listed, layout
from pgn_dl.sources.chessendings.theory import TheoryExtractor

from .conftest import (
    ACCOUNT,
    LUCENA_FEN,
    ROOT,
    SESSION,
    THEORY_ROWS,
    FakeFetcher,
    article_page,
    component,
    drill_page,
    page,
    paragraph,
    replay,
    section,
    stream,
    theory_listing,
    theory_row,
)

LISTING = f"{ROOT}/train"


def lesson_url(slug: str) -> str:
    return f"{ROOT}/train/{slug}"


def parse_listing(tree: object) -> list[Listed]:
    return TheoryExtractor(LISTING).parse_listing(tree)


@pytest.mark.parametrize(
    "target",
    [
        "https://chessendings.com/train",
        "https://chessendings.com/train/",
        "https://www.chessendings.com/train?filter=rook",
        "https://chessendings.com/endgames",
    ],
)
def test_the_theory_page_means_every_lesson(target: str) -> None:
    assert TheoryExtractor(target).slug is None


@pytest.mark.parametrize(
    "target",
    [
        "https://chessendings.com/train/lucena-position",
        "https://chessendings.com/train/lucena-position#step-2",
        "https://chessendings.com/endgames/lucena-position",
        "https://chessendings.com/fr/endgames/lucena-position",
        "lucena-position",
        "Lucena-Position",
    ],
)
def test_a_lesson_url_or_slug_means_that_lesson(target: str) -> None:
    assert TheoryExtractor(target).slug == "lucena-position"


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/train/lucena-position",
        "https://chessendings.com/endgames/theme/rook",
        "https://chessendings.com/practice/lucena-position",
        "lucena position",
        "",
    ],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(ValueError):
        TheoryExtractor(target)


def test_theory_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url("https://chessendings.com/train/lucena-position")
    assert isinstance(found, TheoryExtractor) and found.slug == "lucena-position"
    assert isinstance(
        registry.resolve_url("https://chessendings.com/endgames/king-rook-checkmate"),
        TheoryExtractor,
    )
    assert (
        TheoryExtractor.from_url("https://chessendings.com/endgames/theme/rook") is None
    )


def _paths(rows: tuple[dict, ...] = THEORY_ROWS) -> list[tuple[str, tuple[str, str]]]:
    tree = flight.parse(theory_listing(rows))
    return [(x.slug, path) for x, path in layout(parse_listing(tree))]


def test_lessons_are_numbered_within_families_in_the_site_order() -> None:
    assert _paths() == [
        ("king-queen-checkmate", ("01-checkmates", "001-king-queen-checkmate")),
        ("king-rook-checkmate", ("01-checkmates", "002-king-rook-checkmate")),
        ("opposition-basics", ("02-king-pawn", "001-opposition-basics")),
        ("lucena-position", ("03-rook-endgames", "001-lucena-position")),
        ("philidor-position", ("03-rook-endgames", "002-philidor-position")),
    ]


def test_the_listing_reads_family_level_and_lock() -> None:
    lessons = parse_listing(flight.parse(theory_listing()))
    lucena = next(x for x in lessons if x.slug == "lucena-position")
    assert lucena.group == "Rook endgames"
    assert lucena.headers == {
        "ChessendingsSlug": "lucena-position",
        "ChessendingsTheme": "Rook endgames",
        "ChessendingsLevel": "Class C (1400–1599)",
    }
    assert lucena.locked is False
    assert next(x for x in lessons if x.slug == "philidor-position").locked is True


@pytest.mark.parametrize(
    "rows",
    [
        (theory_row("Not A Slug", "Checkmates", 0),),
        (theory_row("x", "Checkmates", 0) | {"familyOrder": None},),
        (theory_row("x", "Checkmates", 0) | {"motifLabel": ""},),
        ({"familyOrder": 0, "slug": "x"},),
    ],
)
def test_an_unreadable_listing_is_refused(rows: tuple[dict, ...]) -> None:
    with pytest.raises(FetchError):
        parse_listing(flight.parse(theory_listing(rows)))


def test_a_page_without_a_listing_is_refused() -> None:
    with pytest.raises(FetchError, match="no Theory listing"):
        parse_listing(flight.parse(stream(page())))


#: The listing as a Pro account sees it: nothing locked.
PRO_VIEW = tuple(row | {"pro": False} for row in THEORY_ROWS)


@pytest.mark.usefixtures("guest")
async def test_a_guest_gets_the_free_lessons_and_is_told_what_was_left_out() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=False)
    collection, refs = await TheoryExtractor(LISTING).discover(fetcher)
    assert (collection.source, collection.kind, collection.slug) == (
        "chessendings",
        "theory",
        "theory",
    )
    assert "philidor-position" not in [r.id for r in refs]
    # Numbering comes from the whole listing, so signing in later moves nothing.
    assert next(r for r in refs if r.id == "lucena-position").path == (
        "03-rook-endgames",
        "001-lucena-position",
    )
    assert collection.name == (
        "Theory (without its 1 Pro lesson; "
        "run: pgn-dl login chessendings, if your account has Pro)"
    )
    assert fetcher.gets == [LISTING]


@pytest.mark.usefixtures("member")
async def test_a_pro_account_gets_every_lesson_under_the_plain_name() -> None:
    fetcher = FakeFetcher(
        {LISTING: theory_listing(PRO_VIEW, account=ACCOUNT)}, signed_in=True
    )
    collection, refs = await TheoryExtractor(LISTING).discover(fetcher)
    assert [r.id for r in refs] == [
        x.slug for x, _ in layout(parse_listing(flight.parse(theory_listing(PRO_VIEW))))
    ]
    assert collection.name == "Theory"


@pytest.mark.usefixtures("member")
async def test_an_account_without_pro_leaves_the_locked_lessons_out() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing(account=ACCOUNT)}, signed_in=True)
    collection, refs = await TheoryExtractor(LISTING).discover(fetcher)
    assert "philidor-position" not in [r.id for r in refs]
    assert collection.name.endswith("; this account does not have Pro)")


@pytest.mark.usefixtures("member")
async def test_a_session_answered_as_a_guest_is_refused() -> None:
    # A guest's listing locks every Pro lesson, so without this a refused
    # session would pass for an account without Pro.
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=True)
    with pytest.raises(SignedOut, match="session was not accepted"):
        await TheoryExtractor(LISTING).discover(fetcher)


@pytest.mark.usefixtures("guest")
async def test_one_lesson_gets_the_path_a_full_download_gives_it() -> None:
    pages = {LISTING: theory_listing()}
    _, everything = await TheoryExtractor(LISTING).discover(
        FakeFetcher(pages, signed_in=False)
    )
    _, one = await TheoryExtractor("lucena-position").discover(
        FakeFetcher(pages, signed_in=False)
    )
    assert one == [r for r in everything if r.id == "lucena-position"]


@pytest.mark.usefixtures("guest")
async def test_an_unlisted_lesson_is_refused() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=False)
    with pytest.raises(FetchError, match="no Theory lesson 'nope'"):
        await TheoryExtractor("nope").discover(fetcher)


async def _fetch(extractor: TheoryExtractor, fetcher: FakeFetcher, slug: str) -> str:
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, next(r for r in refs if r.id == slug))
    return game_to_pgn(collection, item)


@pytest.mark.usefixtures("guest")
async def test_a_lesson_is_one_annotated_game() -> None:
    pages = {LISTING: theory_listing(), lesson_url("lucena-position"): drill_page()}
    fetcher = FakeFetcher(pages, signed_in=False)
    pgn = await _fetch(TheoryExtractor(LISTING), fetcher, "lucena-position")
    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None and not game.errors
    assert game.headers["ChessendingsSlug"] == "lucena-position"
    assert game.headers["ChessendingsTheme"] == "Rook endgames"
    assert game.headers["ChessendingsLevel"] == "Class C (1400–1599)"
    assert game.headers["Site"] == "chessendings.com"
    assert len(list(game.mainline_moves())) == 4


@pytest.mark.usefixtures("guest")
async def test_a_locked_lesson_asked_for_by_name_fails_without_a_request() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=False)
    with pytest.raises(ProRequired, match="run: pgn-dl login chessendings"):
        await _fetch(TheoryExtractor("philidor-position"), fetcher, "philidor-position")
    assert fetcher.gets == [LISTING]


@pytest.mark.usefixtures("member")
async def test_once_a_page_comes_back_signed_out_the_rest_are_not_requested(
    tmp_path: Path,
) -> None:
    rows = tuple(theory_row(f"lesson-{n}", "Checkmates", n) for n in range(3))
    pages = {LISTING: theory_listing(rows, account=ACCOUNT)}
    pages[lesson_url("lesson-0")] = drill_page(account=ACCOUNT)
    pages[lesson_url("lesson-1")] = drill_page()  # the session stopped counting
    pages[lesson_url("lesson-2")] = drill_page()
    fetcher = FakeFetcher(pages, signed_in=True)
    lines: list[str] = []
    report = await run(
        TheoryExtractor(LISTING),
        fetcher,
        Options(out=tmp_path, concurrency=1, delay=0),
        echo=lines.append,
    )
    assert lesson_url("lesson-2") not in fetcher.gets
    assert (report.written, report.failed) == (1, 2)
    assert sum("not requested" in line for line in lines) == 1


@pytest.mark.usefixtures("guest")
async def test_an_open_lesson_without_a_drill_means_the_site_changed() -> None:
    pages = {LISTING: theory_listing(), lesson_url("lucena-position"): stream(page())}
    fetcher = FakeFetcher(pages, signed_in=False)
    with pytest.raises(FetchError, match="no drill on its page; the site has changed"):
        await _fetch(TheoryExtractor(LISTING), fetcher, "lucena-position")


@pytest.mark.usefixtures("guest")
async def test_a_page_rendered_in_another_language_is_refused() -> None:
    # The cookie and header pin English; should the server choose otherwise
    # (an account's own preference, say), every file would be in French with
    # no error at all.
    fetcher = FakeFetcher({LISTING: stream(page(locale="fr"))}, signed_in=False)
    with pytest.raises(FetchError, match="'fr' rather than English"):
        await TheoryExtractor(LISTING).discover(fetcher)


async def test_concurrent_requests_ask_for_the_session_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two requests finding the token stale together would both spend the one
    # refresh token.
    active, most = 0, 0

    async def slow_cookie() -> str:
        nonlocal active, most
        active += 1
        most = max(most, active)
        await asyncio.sleep(0)
        active -= 1
        return SESSION

    monkeypatch.setattr(auth, "session_cookie", slow_cookie)
    fetcher = FakeFetcher({LISTING: theory_listing(account=ACCOUNT)}, signed_in=True)
    client = Client(fetcher, signed_in=True)
    await asyncio.gather(*(client.page("/train") for _ in range(3)))
    assert most == 1


def endgame_page(*essay: str, guide: str | None = "lucena-guide") -> str:
    """A lesson's public page: its title, a link to a guide, The theory section."""
    lede = ["White to play and win", "Build a bridge."]
    theory = section(
        "The theory",
        [
            paragraph("Watch the method: press play to see the winning idea run."),
            replay(LUCENA_FEN, ["c1d1", "d8e7"]),
        ],
        [paragraph(text, key=str(n)) for n, text in enumerate(essay)],
    )
    body = [theory]
    if guide is not None:
        body.insert(0, component({"href": f"/guides/{guide}", "children": "the guide"}))
    return article_page("Lucena Position Practice", lede, body)


def endgame_url(slug: str) -> str:
    return f"{ROOT}/endgames/{slug}"


async def _lesson(pages: dict[str, str]) -> chess.pgn.Game:
    pages = {
        LISTING: theory_listing(),
        lesson_url("lucena-position"): drill_page(),
    } | pages
    fetcher = FakeFetcher(pages, signed_in=False)
    pgn = await _fetch(TheoryExtractor("lucena-position"), fetcher, "lucena-position")
    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None and not game.errors
    return game


@pytest.mark.usefixtures("guest")
async def test_the_lessons_essay_joins_its_comment_after_the_intro() -> None:
    game = await _lesson(
        {
            endgame_url("lucena-position"): endgame_page(
                "The bridge is the method.", "It always works."
            )
        }
    )
    # The essay is what follows the replay; the line above the replay, and the
    # replay itself (the lesson's own mainline), are not repeated.
    assert game.comment.startswith(
        "White to play and win.\n\nBuild a bridge.\n\n"
        "The bridge is the method.\n\nIt always works.\n\nNever play Ra1??"
    )
    assert "Watch the method" not in game.comment
    assert game.headers["ChessendingsGuide"] == "lucena-guide"


@pytest.mark.usefixtures("guest")
async def test_a_lesson_without_a_public_page_downloads_without_an_essay() -> None:
    game = await _lesson({})  # the fake answers 404
    assert game.comment.startswith("White to play and win.\n\nBuild a bridge.\n\nNever")
    assert "ChessendingsGuide" not in game.headers


@pytest.mark.usefixtures("guest")
async def test_a_public_page_this_module_cannot_read_costs_only_the_essay() -> None:
    game = await _lesson(
        {endgame_url("lucena-position"): stream(page(paragraph("Redesigned.")))}
    )
    assert "Redesigned" not in game.comment
    assert len(list(game.mainline_moves())) == 4


@pytest.mark.usefixtures("guest")
async def test_a_public_page_that_fails_otherwise_fails_the_lesson() -> None:
    class Down(FakeFetcher):
        async def get_text(self, url, *, headers):
            if "/endgames/" in url:
                raise FetchError(f"{url} failed: 503 (HTML page)", status=503)
            return await super().get_text(url, headers=headers)

    pages = {LISTING: theory_listing(), lesson_url("lucena-position"): drill_page()}
    with pytest.raises(FetchError, match="503"):
        await _fetch(
            TheoryExtractor("lucena-position"),
            Down(pages, signed_in=False),
            "lucena-position",
        )
