"""Theory: targets, the listing, layout, guests and members, and Pro lessons."""

from __future__ import annotations

import asyncio
import io

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.pgn import game_to_pgn
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
    return TheoryExtractor("lucena-position").parse_listing(tree)


@pytest.mark.parametrize(
    "target",
    [
        "https://chessendings.com/train",
        "https://chessendings.com/train/",
        "https://www.chessendings.com/train?filter=rook",
        "https://chessendings.com/endgames",
    ],
)
def test_theory_s_own_url_is_refused(target: str) -> None:
    with pytest.raises(
        WholeSection, match="is all of Theory; give one Theory lesson's URL"
    ):
        TheoryExtractor(target)


def test_the_registry_refuses_theory_s_own_url() -> None:
    with pytest.raises(WholeSection, match="is all of Theory"):
        registry.resolve_url("https://chessendings.com/train")


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
    with pytest.raises(
        ValueError, match="could not read a chessendings Theory lesson"
    ) as refused:
        TheoryExtractor(target)
    assert not isinstance(refused.value, WholeSection)


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
async def test_a_lesson_is_numbered_from_the_whole_listing() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=False)
    collection, refs = await TheoryExtractor("lucena-position").discover(fetcher)
    assert (collection.source, collection.kind, collection.slug, collection.name) == (
        "chessendings",
        "theory",
        "theory",
        "Theory",
    )
    assert [(r.id, r.path) for r in refs] == [
        ("lucena-position", ("03-rook-endgames", "001-lucena-position"))
    ]
    assert fetcher.gets == [LISTING]


@pytest.mark.usefixtures("guest")
async def test_a_guest_s_numbers_count_the_pro_lessons() -> None:
    rows = (
        theory_row("pro-ending", "Rook endgames", 0, pro=True),
        theory_row("free-ending", "Rook endgames", 1),
    )
    fetcher = FakeFetcher({LISTING: theory_listing(rows)}, signed_in=False)
    _, refs = await TheoryExtractor("free-ending").discover(fetcher)
    # Numbered as for a Pro account, so signing in later moves nothing.
    assert [r.path for r in refs] == [("01-rook-endgames", "002-free-ending")]


@pytest.mark.usefixtures("member")
async def test_a_pro_account_downloads_a_pro_lesson() -> None:
    pages = {
        LISTING: theory_listing(PRO_VIEW, account=ACCOUNT),
        lesson_url("philidor-position"): drill_page(account=ACCOUNT),
    }
    fetcher = FakeFetcher(pages, signed_in=True)
    pgn = await _fetch(TheoryExtractor("philidor-position"), fetcher)
    assert '[ChessendingsSlug "philidor-position"]' in pgn


@pytest.mark.usefixtures("member")
async def test_an_account_without_pro_is_told_so_for_a_pro_lesson() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing(account=ACCOUNT)}, signed_in=True)
    with pytest.raises(ProRequired, match="this account does not have Pro"):
        await _fetch(TheoryExtractor("philidor-position"), fetcher)
    assert fetcher.gets == [LISTING]


@pytest.mark.usefixtures("member")
async def test_a_session_answered_as_a_guest_is_refused() -> None:
    # A guest's listing locks every Pro lesson, so without this a refused
    # session would pass for an account without Pro.
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=True)
    with pytest.raises(SignedOut, match="session was not accepted"):
        await TheoryExtractor("lucena-position").discover(fetcher)


@pytest.mark.usefixtures("guest")
async def test_an_unlisted_lesson_is_refused() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=False)
    with pytest.raises(FetchError, match="no Theory lesson 'nope'"):
        await TheoryExtractor("nope").discover(fetcher)


async def _fetch(extractor: TheoryExtractor, fetcher: FakeFetcher) -> str:
    collection, [ref] = await extractor.discover(fetcher)
    return game_to_pgn(collection, await extractor.fetch(fetcher, ref))


@pytest.mark.usefixtures("guest")
async def test_a_lesson_is_one_annotated_game() -> None:
    pages = {LISTING: theory_listing(), lesson_url("lucena-position"): drill_page()}
    fetcher = FakeFetcher(pages, signed_in=False)
    pgn = await _fetch(TheoryExtractor("lucena-position"), fetcher)
    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None and not game.errors
    assert game.headers["ChessendingsSlug"] == "lucena-position"
    assert game.headers["ChessendingsTheme"] == "Rook endgames"
    assert game.headers["ChessendingsLevel"] == "Class C (1400–1599)"
    assert game.headers["Site"] == "chessendings.com"
    assert len(list(game.mainline_moves())) == 4


@pytest.mark.usefixtures("guest")
async def test_a_locked_lesson_fails_without_a_request() -> None:
    fetcher = FakeFetcher({LISTING: theory_listing()}, signed_in=False)
    with pytest.raises(ProRequired, match="run: pgn-dl login chessendings"):
        await _fetch(TheoryExtractor("philidor-position"), fetcher)
    assert fetcher.gets == [LISTING]


@pytest.mark.usefixtures("member")
async def test_a_lesson_page_answered_as_a_guest_is_refused() -> None:
    pages = {
        LISTING: theory_listing(account=ACCOUNT),
        lesson_url("lucena-position"): drill_page(),  # the session stopped counting
    }
    fetcher = FakeFetcher(pages, signed_in=True)
    with pytest.raises(SignedOut, match="session was not accepted"):
        await _fetch(TheoryExtractor("lucena-position"), fetcher)


@pytest.mark.usefixtures("guest")
async def test_an_open_lesson_without_a_drill_means_the_site_changed() -> None:
    pages = {LISTING: theory_listing(), lesson_url("lucena-position"): stream(page())}
    fetcher = FakeFetcher(pages, signed_in=False)
    with pytest.raises(FetchError, match="no drill on its page; the site has changed"):
        await _fetch(TheoryExtractor("lucena-position"), fetcher)


@pytest.mark.usefixtures("guest")
async def test_a_page_rendered_in_another_language_is_refused() -> None:
    # The cookie and header pin English; should the server choose otherwise
    # (an account's own preference, say), every file would be in French with
    # no error at all.
    fetcher = FakeFetcher({LISTING: stream(page(locale="fr"))}, signed_in=False)
    with pytest.raises(FetchError, match="'fr' rather than English"):
        await TheoryExtractor("lucena-position").discover(fetcher)


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
    pgn = await _fetch(TheoryExtractor("lucena-position"), fetcher)
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
        await _fetch(TheoryExtractor("lucena-position"), Down(pages, signed_in=False))
