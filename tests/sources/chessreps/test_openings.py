"""Target parsing, tree building, and the extractor's discover/fetch."""

from __future__ import annotations

import io
from dataclasses import replace
from typing import Any

import chess
import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import MoveNode
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.sources.chessreps import openings
from pgn_dl.sources.chessreps.openings import OpeningsExtractor
from pgn_dl.sources.chessreps.parser import Course

from .conftest import (
    FakeFetcher,
    course_page,
    data_json,
    signed_in_layout,
    signed_out_layout,
)

DATA_URL = "https://www.chessreps.com/opening/test-rep/__data.json"


def _fetcher(page: dict, layout: dict | None = None) -> FakeFetcher:
    body = data_json(signed_in_layout() if layout is None else layout, page)
    return FakeFetcher({DATA_URL: body})


def make_course(**overrides: Any) -> Course:
    base = Course(
        id="abc1234",
        name="Test Repertoire",
        description="Blurb.",
        player_side="w",
        lines=("1. e4 c5 2. c3 Nc6", "1. e4 c5 2. c3 d5"),
        line_names={"1. e4 c5 2. c3 Nc6": "Old Main Line"},
        descriptions={},
        evals={},
        trial=False,
    )
    return replace(base, **overrides)


@pytest.mark.parametrize(
    "target",
    [
        "https://www.chessreps.com/opening/alapin-sicilian-repertoire-ilg8hms",
        "https://chessreps.com/opening/alapin-sicilian-repertoire-ilg8hms/",
        "https://www.chessreps.com/es/opening/alapin-sicilian-repertoire-ilg8hms",
        "https://www.chessreps.com/opening/alapin-sicilian-repertoire-ilg8hms?ref=x",
        "https://www.chessreps.com/opening/alapin-sicilian-repertoire-ilg8hms#board",
        "alapin-sicilian-repertoire-ilg8hms",
    ],
)
def test_slug_from_accepts_urls_and_bare_slugs(target: str) -> None:
    assert openings.slug_from(target) == "alapin-sicilian-repertoire-ilg8hms"


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/opening/london",
        "https://www.chessreps.com/puzzles/london",
        "not a slug at all",
        "",
    ],
)
def test_slug_from_rejects_what_is_not_an_opening(target: str) -> None:
    with pytest.raises(ValueError):
        openings.slug_from(target)


def _sans(node: MoveNode) -> list[str | None]:
    # `san` is None only on a root, which never sits in `children`.
    return [c.san for c in node.children]


def test_build_tree_merges_shared_prefixes() -> None:
    tree = openings.build_tree(make_course())
    assert _sans(tree) == ["e4"]
    e4 = tree.children[0]
    c5 = e4.children[0]
    c3 = c5.children[0]
    # The two lines diverge only at black's second reply.
    assert _sans(c3) == ["Nc6", "d5"]


def test_build_tree_keeps_the_first_line_as_mainline() -> None:
    course = make_course(
        lines=("1. e4 e5", "1. e4 c5", "1. d4 d5"),
        line_names={},
    )
    tree = openings.build_tree(course)
    assert _sans(tree) == ["e4", "d4"]
    assert _sans(tree.children[0]) == ["e5", "c5"]


def test_build_tree_attaches_descriptions_by_full_fen() -> None:
    board = chess.Board()
    board.push_san("e4")
    course = make_course(
        lines=("1. e4 c5",),
        line_names={},
        descriptions={board.fen(): "The king's pawn."},
    )
    tree = openings.build_tree(course)
    assert tree.children[0].post_comment.startswith("The king's pawn.")


def test_build_tree_attaches_evals_by_four_field_fen() -> None:
    board = chess.Board()
    board.push_san("e4")
    fen4 = " ".join(board.fen().split()[:4])
    course = make_course(
        lines=("1. e4 c5",),
        line_names={},
        evals={fen4: {"type": "score", "depth": 70, "score": 18}},
    )
    tree = openings.build_tree(course)
    assert "[%eval 0.18]" in tree.children[0].post_comment


def test_build_tree_tags_a_named_line_on_its_final_move() -> None:
    tree = openings.build_tree(make_course())
    nc6 = tree.children[0].children[0].children[0].children[0]
    assert nc6.san == "Nc6"
    assert "(line: Old Main Line)" in nc6.post_comment
    d5 = tree.children[0].children[0].children[0].children[1]
    assert "(line:" not in d5.post_comment


def test_build_tree_fails_loudly_on_an_unplayable_move() -> None:
    course = make_course(lines=("1. e4 Nf9",), line_names={})
    with pytest.raises(FetchError) as exc:
        openings.build_tree(course)
    assert "Nf9" in str(exc.value)


def test_a_moveless_line_fails_rather_than_writing_an_empty_game() -> None:
    # A headers-only game would be written once and skipped by every later
    # run, so the item must fail instead.
    course = make_course(lines=("1. e4 e5", "1."), line_names={})
    with pytest.raises(FetchError) as exc:
        openings.build_line(course, 2)
    assert "line 2" in str(exc.value)
    with pytest.raises(FetchError):
        openings.build_tree(course)


def test_build_line_is_a_bare_chain_without_the_tag() -> None:
    root = openings.build_line(make_course(), 1)
    sans = []
    node = root
    while node.children:
        assert len(node.children) == 1
        node = node.children[0]
        sans.append(node.san)
    assert sans == ["e4", "c5", "c3", "Nc6"]
    assert "(line:" not in node.post_comment


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"type": "score", "depth": 32, "score": 286}, "[%eval 2.86]"),
        ({"type": "score", "depth": 10, "score": -50}, "[%eval -0.50]"),
        ({"type": "mate", "depth": 20, "score": 4}, "[%eval #4]"),
        ({"type": "unknown", "score": 1}, ""),
        ({"type": "score", "score": "not a number"}, ""),
        ("not a dict", ""),
        (None, ""),
    ],
)
def test_format_eval(entry: object, expected: str) -> None:
    assert openings._format_eval(entry) == expected


async def test_discover_lists_the_merged_game_and_every_line(stub_cookie) -> None:
    ex = OpeningsExtractor("test-rep")
    collection, refs = await ex.discover(_fetcher(course_page()))
    assert collection.source == "chessreps" and collection.kind == "openings"
    assert collection.slug == "test-rep"
    assert collection.name == "Test Repertoire"
    assert [r.path for r in refs] == [
        ("test-repertoire",),
        ("lines", "001-old-main-line"),
        ("lines", "002-line"),  # unnamed line falls back
    ]
    assert refs[0].id == "abc1234"
    assert refs[1].id == "line:1"


async def test_fetch_renders_the_merged_game_and_a_line(stub_cookie) -> None:
    board = chess.Board()
    board.push_san("e4")
    page = course_page(descriptions={board.fen(): "The king's pawn."})
    ex = OpeningsExtractor("test-rep")
    collection, refs = await ex.discover(_fetcher(page))

    merged = await ex.fetch(_fetcher(page), refs[0])
    text = game_to_pgn(collection, merged)
    assert '[Event "Test Repertoire"]' in text
    assert '[White "Test Repertoire"]' in text  # playerSide "w"
    assert '[ChessrepsOpeningId "abc1234"]' in text
    assert "The king's pawn." in text
    assert "A course blurb." in text  # course description as the game comment
    assert "line: Old Main Line" in text

    line = await ex.fetch(_fetcher(page), refs[1])
    line_text = game_to_pgn(collection, line)
    assert '[Event "Old Main Line"]' in line_text
    assert '[ChessrepsOpening "Test Repertoire"]' in line_text
    game = chess.pgn.read_game(io.StringIO(line_text))
    assert game is not None and not game.errors


async def test_fetch_needs_no_second_request(stub_cookie) -> None:
    fetcher = _fetcher(course_page())
    ex = OpeningsExtractor("test-rep")
    _, refs = await ex.discover(fetcher)
    await ex.fetch(fetcher, refs[0])
    await ex.fetch(fetcher, refs[1])
    assert len(fetcher.gets) == 1


async def test_a_signed_out_answer_is_actionable(stub_cookie) -> None:
    ex = OpeningsExtractor("test-rep")
    with pytest.raises(FetchError) as exc:
        await ex.discover(_fetcher(course_page(), layout=signed_out_layout()))
    assert "signed out" in str(exc.value)


async def test_trial_content_gets_its_own_slug(stub_cookie) -> None:
    ex = OpeningsExtractor("test-rep")
    collection, _ = await ex.discover(_fetcher(course_page(isUserCourseTrial=True)))
    assert collection.slug == "test-rep-trial"


async def test_a_course_named_all_steers_clear_of_the_combined_file(
    stub_cookie,
) -> None:
    ex = OpeningsExtractor("test-rep")
    _, refs = await ex.discover(_fetcher(course_page(displayName="All")))
    assert refs[0].path == ("all-repertoire",)


async def test_a_black_repertoire_names_black(stub_cookie) -> None:
    page = course_page(playerSide="b")
    ex = OpeningsExtractor("test-rep")
    collection, refs = await ex.discover(_fetcher(page))
    item = await ex.fetch(_fetcher(page), refs[0])
    text = game_to_pgn(collection, item)
    assert '[Black "Test Repertoire"]' in text


def test_the_registry_owns_chessreps_urls() -> None:
    got = registry.resolve_url(
        "https://www.chessreps.com/opening/alapin-sicilian-repertoire-ilg8hms"
    )
    assert isinstance(got, OpeningsExtractor)
    assert "chessreps:openings" in [c.key for c in registry.iter_extractors()]
