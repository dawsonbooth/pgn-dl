"""Lichess's opening pages, rebuilt from the opening table and the explorer.

The table rows are invented in scalachess's generated format, their positions
and moves computed here so they stay consistent; the explorer answers are
invented in its documented shape. Nothing is captured.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

import chess
import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import Blocked, FetchError
from pgn_dl.sources.lichess import openings
from pgn_dl.sources.lichess.openings import (
    EXPLORER,
    GAMES_EXPORT,
    LILA_DEPENDENCIES,
    NOT_IN_DATABASE,
    SETTINGS,
    Opening,
    OpeningsExtractor,
    Table,
    _round,
    name_to_key,
    opening_game,
    order_games,
    parse_table,
    results,
    section_name,
    target_from,
)

from .conftest import failure

TOKEN = "DUMMY-LICHESS-TOKEN-DO-NOT-LEAK"
VERSION = "18.1.0"
PARTS = f"{openings.SCALACHESS}/{VERSION}/core/src/main/scala/opening"


def row(eco: str, name: str, sans: str) -> str:
    """One table row as scalachess generates it, from the line's SAN."""
    board = chess.Board()
    ucis = []
    for san in sans.split():
        move = board.parse_san(san)
        ucis.append(move.uci())
        board.push(move)
    numbered = chess.Board().variation_san([chess.Move.from_uci(u) for u in ucis])
    return (
        f'Opening("{eco}", "{name}", "{board.epd()}", "{" ".join(ucis)}", '
        f'"{numbered}"),'
    )


def part(*rows: str) -> str:
    body = "\n".join(rows)
    return (
        "package chess.opening\n\n"
        "// Generated from https://github.com/lichess-org/chess-openings\n"
        "// format: off\n"
        "private[opening] def openingDbPartA: Vector[Opening] = Vector(\n"
        f"{body}\n)\n"
    )


TABLE_A = part(
    row("A40", "Queen's Pawn Game", "d4"),
    row("A48", "London System", "d4 Nf6 Nf3 g6 Bf4"),
    row("D02", "London System", "d4 d5 Nf3 Nf6 Bf4 e6 e3"),
)
TABLE_B = part(
    row("B00", "King's Pawn Game", "e4"),
    row("B20", "Sicilian Defense", "e4 c5"),
    # A longer line of the same name: not the page, but it names 2. Nf3.
    row("B27", "Sicilian Defense", "e4 c5 Nf3"),
    row("B23", "Sicilian Defense: Closed", "e4 c5 Nc3"),
    row("B20", "Sicilian Defense: Bowdler Attack", "e4 c5 Bc4"),
)
TABLE_D = part(row("D80", "Grünfeld Defense", "d4 Nf6 c4 g6 Nc3 d5"))
EMPTY = part()

DEPENDENCIES = f"""object Dependencies {{
  object chess:
    val version = "{VERSION}"
    val org = "com.github.lichess-org.scalachess"
}}"""


def explorer_answer(
    *,
    moves: list[dict[str, Any]] | None = None,
    top: tuple[str, ...] = (),
    recent: tuple[str, ...] = (),
    totals: tuple[int, int, int] = (480, 40, 480),
) -> str:
    white, draws, black = totals
    return json.dumps(
        {
            "opening": None,
            "white": white,
            "draws": draws,
            "black": black,
            "moves": moves or [],
            "topGames": [{"uci": "a2a3", "id": i} for i in top],
            "recentGames": [{"uci": "a2a3", "id": i} for i in recent],
        }
    )


def next_move(uci: str, san: str, white: int, draws: int, black: int) -> dict:
    return {
        "uci": uci,
        "san": san,
        "averageRating": 1800,
        "white": white,
        "draws": draws,
        "black": black,
        "game": None,
        "opening": None,
    }


def exported(*ids: str) -> str:
    return "\n\n\n".join(
        f'[Event "Rated blitz game"]\n[Site "https://lichess.org/game/{i}"]\n'
        f'[GameId "{i}"]\n[Result "1-0"]\n\n'
        "1. e4 { [%clk 0:03:00] } 1... c5 { [%clk 0:03:00] } 1-0"
        for i in ids
    )


def opening_page(wiki: str) -> str:
    """An opening page as lila serves it, reduced to the parts around its wiki."""
    return (
        '<!DOCTYPE html><html><body><main class="page box box-pad opening">'
        '<div class="opening__intro"><div class="opening__intro__content">'
        f'<div class="opening__wiki"><div class="opening__wiki__markup">{wiki}'
        "</div></div></div></div></main></body></html>"
    )


#: A page with no description, as most are.
NO_DESCRIPTION = opening_page(
    '<div class="opening__wiki__markup__placeholder">'
    "No description of the opening, yet.</div>"
)


class Explorer:
    """Lichess for these tests: GitHub's table, the explorer, the game export,
    and the pages, through the window."""

    def __init__(
        self,
        answers: Mapping[str, str | Exception] | None = None,
        export: str = "",
        pages: Mapping[str, str | Exception] | None = None,
    ) -> None:
        self.answers = dict(answers or {})  # by the ``play`` parameter
        self.export = export
        self.pages = dict(pages or {})  # by page key; default NO_DESCRIPTION
        self.gets: list[tuple[str, dict[str, str]]] = []
        self.posts: list[tuple[str, dict[str, str], str]] = []
        self.read: list[str] = []  # every page read, in order

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.gets.append((url, dict(headers)))
        files = {
            LILA_DEPENDENCIES: DEPENDENCIES,
            f"{PARTS}/OpeningDbPartA.scala": TABLE_A,
            f"{PARTS}/OpeningDbPartB.scala": TABLE_B,
            f"{PARTS}/OpeningDbPartC.scala": EMPTY,
            f"{PARTS}/OpeningDbPartD.scala": TABLE_D,
            f"{PARTS}/OpeningDbPartE.scala": EMPTY,
        }
        if url in files:
            return files[url]
        if url.startswith(f"{EXPLORER}/lichess?"):
            play = parse_qs(urlsplit(url).query).get("play", [""])[0]
            answer = self.answers.get(play, explorer_answer())
            if isinstance(answer, Exception):
                raise answer
            return answer
        raise AssertionError(f"unexpected GET {url}")

    async def post_text(
        self, url: str, *, headers: Mapping[str, str], body: str
    ) -> str:
        self.posts.append((url, dict(headers), body))
        return self.export

    async def get_page(self, url: str) -> str:
        self.read.append(url)
        prefix = "https://lichess.org/opening/"
        if not url.startswith(prefix):
            raise AssertionError(f"unexpected page {url}")
        answer = self.pages.get(url.removeprefix(prefix), NO_DESCRIPTION)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("the openings send no JSON")

    def explorer_queries(self) -> list[dict[str, list[str]]]:
        return [
            parse_qs(urlsplit(url).query, keep_blank_values=True)
            for url, _ in self.gets
            if url.startswith(EXPLORER)
        ]


@pytest.fixture(autouse=True)
def saved_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openings.auth, "load_token", lambda: TOKEN)


def table() -> Table:
    return parse_table([TABLE_A, TABLE_B, EMPTY, TABLE_D, EMPTY])


# The table.


def test_a_key_is_the_name_as_scalachess_makes_it() -> None:
    assert name_to_key("Queen's Gambit Declined") == "Queens_Gambit_Declined"
    assert name_to_key("Caro-Kann Defense") == "Caro-Kann_Defense"
    assert name_to_key("Grünfeld Defense") == "Grunfeld_Defense"
    assert name_to_key("Sicilian Defense: Closed") == "Sicilian_Defense_Closed"
    # A letter with no decomposition drops out, as Java's ASCII \w drops it.
    assert name_to_key("Hjørring Countergambit") == "Hjrring_Countergambit"


def test_a_name_has_one_page_and_its_line_is_the_shortest() -> None:
    pages = table().pages
    assert pages["London_System"].pgn == "1. d4 Nf6 2. Nf3 g6 3. Bf4"
    assert set(pages) == {
        "Queens_Pawn_Game",
        "London_System",
        "Kings_Pawn_Game",
        "Sicilian_Defense",
        "Sicilian_Defense_Closed",
        "Sicilian_Defense_Bowdler_Attack",
        "Grunfeld_Defense",
    }


def test_of_two_equally_short_lines_the_later_one_is_the_page() -> None:
    first = Opening("A00", "X", "fen1", "e2e4", "1. e4")
    second = Opening("A00", "X", "fen2", "d2d4", "1. d4")
    assert Table((first, second)).pages["X"] is second


async def test_discovery_reads_lila_s_pinned_version_then_the_table() -> None:
    fetcher = Explorer()
    await OpeningsExtractor("https://lichess.org/opening").discover(fetcher)
    urls = [url for url, _ in fetcher.gets]
    assert urls[0] == LILA_DEPENDENCIES
    assert urls[1:6] == [f"{PARTS}/OpeningDbPart{p}.scala" for p in "ABCDE"]


def test_a_table_with_no_rows_is_refused() -> None:
    with pytest.raises(FetchError, match="no openings"):
        parse_table([EMPTY])


# Names of continuations, as lila's own tests state them.


@pytest.mark.parametrize(
    ("prev", "after", "expected"),
    [
        ("A", "A", "A"),
        ("A", "A: B", "B"),
        ("A", "A: B, C", "B"),
        ("A: B", "A: B, C", "C"),
        ("", "A", "A"),
        ("", "A:B", "A"),
        ("Polish Opening", "Polish Opening", "Polish Opening"),
        (
            "Polish Opening: King's Indian Variation",
            "Polish Opening: King's Indian Variation, Schiffler Attack",
            "Schiffler Attack",
        ),
        ("A", "B", "B"),
        ("A:B", "Z:B", "Z"),
        (
            "Slav Defense",
            "Semi-Slav Defense: Accelerated Move Order",
            "Semi-Slav Defense",
        ),
        ("A:B", "A:Z", "Z"),
        ("A:B,C", "A:Y,Z", "Y"),
        ("A:B,C,D", "A:Z", "Z"),
        ("A:B", "A", "A"),
        ("A:B,C", "A:B", "B"),
        (
            "Semi-Slav Defense: Accelerated Move Order",
            "Semi-Slav Defense",
            "Semi-Slav Defense",
        ),
    ],
)
def test_a_continuation_is_named_as_lila_names_it(
    prev: str, after: str, expected: str
) -> None:
    assert section_name(prev, after) == expected


# Targets.


@pytest.mark.parametrize(
    "target", ["https://lichess.org/opening", "https://lichess.org/opening/"]
)
def test_the_openings_url_names_the_openings_page_itself(target: str) -> None:
    assert target_from(target).key is None


@pytest.mark.parametrize("target", ["https://lichess.org/opening/tree", "tree"])
def test_the_tree_of_every_opening_is_refused(target: str) -> None:
    with pytest.raises(
        WholeSection, match="is the tree of every opening; give one opening's"
    ):
        target_from(target)


def test_the_registry_refuses_the_tree_s_url() -> None:
    with pytest.raises(WholeSection, match="is the tree of every opening"):
        registry.resolve_url("https://lichess.org/opening/tree")


def test_a_page_url_or_a_bare_key_names_one_opening() -> None:
    assert target_from("https://lichess.org/opening/London_System").key == (
        "London_System"
    )
    assert target_from("London_System").key == "London_System"
    # lila reads "Defence" as "Defense".
    assert target_from("https://lichess.org/opening/Sicilian_Defence").key == (
        "Sicilian_Defense"
    )


def test_a_page_url_keeps_the_moves_it_carries() -> None:
    target = target_from(
        "https://lichess.org/opening/Sicilian_Defense_Closed/e4_c5_Nc3"
    )
    assert target.key == "Sicilian_Defense_Closed"
    assert target.line == ("e4", "c5", "Nc3")


def test_anything_else_is_not_an_opening_target() -> None:
    with pytest.raises(ValueError, match="could not read a lichess opening") as refused:
        target_from("https://lichess.org/study/abcdefgh")
    assert not isinstance(refused.value, WholeSection)


@pytest.mark.parametrize(
    "target",
    ["https://lichess.org/opening?q=sicilian", "https://lichess.org/opening/?q=caro"],
)
def test_a_search_of_the_openings_is_refused(target: str) -> None:
    with pytest.raises(WholeSection, match="is a search of the openings"):
        target_from(target)


# Discovery.


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ("Sicilian_Defense_Closed", ("sicilian-defense", "sicilian-defense-closed")),
        ("Grunfeld_Defense", ("grunfeld-defense", "grunfeld-defense")),
        ("Queens_Pawn_Game", ("queens-pawn-game", "queens-pawn-game")),
        ("London_System", ("london-system", "london-system")),
    ],
)
async def test_a_page_is_written_in_its_family_s_directory_unnumbered(
    key: str, path: tuple[str, str]
) -> None:
    collection, refs = await OpeningsExtractor(key).discover(Explorer())
    assert collection.slug == "chess-openings"
    assert [(ref.id, ref.path) for ref in refs] == [(key, path)]


async def test_the_openings_url_downloads_the_openings_page_as_index() -> None:
    _, refs = await OpeningsExtractor("https://lichess.org/opening").discover(
        Explorer()
    )
    assert [(ref.id, ref.path) for ref in refs] == [("index", ("index",))]


async def test_the_openings_page_lists_the_first_moves_by_family() -> None:
    answer = explorer_answer(
        moves=[
            next_move("e2e4", "e4", 450, 50, 500),
            next_move("d2d4", "d4", 300, 40, 260),
            next_move("h2h4", "h4", 50, 0, 50),
        ]
    )  # 1700 games across the moves listed
    fetcher = Explorer({"": answer})
    extractor = OpeningsExtractor("https://lichess.org/opening")
    _, refs = await extractor.discover(fetcher)
    asked = len(fetcher.gets)
    item = await extractor.fetch(fetcher, refs[0])
    assert len(fetcher.gets) == asked  # discovery's probe already holds it
    assert item.pgn is not None
    text = flat(item.pgn)
    assert '[Event "Chess openings"]' in text
    assert '[Site "lichess.org"]' in text
    assert f"{{ {SETTINGS} }} 1. e4 {{ King's Pawn Game. Popularity 59%." in text
    assert "( 1. d4 { Queen's Pawn Game. Popularity 35%." in text
    assert "( 1. h4 { Popularity 6%." in text
    assert fetcher.posts == []  # the page shows no example games
    assert fetcher.read == []  # nor a description


async def test_one_page_is_found_by_key_ignoring_case() -> None:
    _, refs = await OpeningsExtractor("london_system").discover(Explorer())
    assert [ref.id for ref in refs] == ["London_System"]


async def test_a_continuation_link_to_a_named_opening_is_that_opening() -> None:
    target = "https://lichess.org/opening/Sicilian_Defense_Closed/e4_c5_Nc3"
    _, refs = await OpeningsExtractor(target).discover(Explorer())
    assert [ref.id for ref in refs] == ["Sicilian_Defense_Closed"]


async def test_a_line_beyond_a_named_opening_is_refused() -> None:
    target = "https://lichess.org/opening/Sicilian_Defense/e4_c5_d3"
    with pytest.raises(FetchError, match="does not reach Sicilian Defense"):
        await OpeningsExtractor(target).discover(Explorer())


async def test_an_opening_lichess_does_not_have_is_refused() -> None:
    with pytest.raises(FetchError, match="no opening page"):
        await OpeningsExtractor("Nonexistent_Gambit").discover(Explorer())


async def test_discovery_needs_the_saved_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> str:
        raise FetchError("no saved lichess credentials; run: pgn-dl login lichess")

    monkeypatch.setattr(openings.auth, "load_token", missing)
    fetcher = Explorer()
    with pytest.raises(FetchError, match="pgn-dl login lichess"):
        await OpeningsExtractor("https://lichess.org/opening").discover(fetcher)
    assert fetcher.gets == []


async def test_a_refused_token_stops_discovery_with_the_login_hint() -> None:
    refused = FetchError("explorer failed: 401", status=401)
    fetcher = Explorer({"": refused})
    with pytest.raises(FetchError, match="refused the saved token") as exc:
        await OpeningsExtractor("https://lichess.org/opening").discover(fetcher)
    assert "pgn-dl login lichess" in str(exc.value)
    assert TOKEN not in str(exc.value)


# One opening's file.


async def fetch_one(key: str, fetcher: Explorer) -> str:
    extractor = OpeningsExtractor(key)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn is not None
    return item.pgn


def flat(pgn: str) -> str:
    """The text with python-chess's line wrapping undone."""
    return " ".join(pgn.split())


async def test_the_explorer_is_asked_what_lila_asks_for_a_page() -> None:
    fetcher = Explorer()
    await fetch_one("Sicilian_Defense", fetcher)
    asked = fetcher.explorer_queries()[-1]
    assert asked == {
        "play": ["e2e4,c7c5"],
        "since": ["2017-01"],
        "ratings": ["1000,1200,1400,1600,1800,2000,2200,2500"],
        "speeds": ["bullet,blitz,rapid,classical,correspondence"],
        "history": ["yes"],
    }
    headers = [h for url, h in fetcher.gets if url.startswith(EXPLORER)][-1]
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_the_page_s_line_results_and_settings_are_written() -> None:
    pgn = await fetch_one("Sicilian_Defense", Explorer())
    assert '[Event "Sicilian Defense"]' in pgn
    assert '[Site "lichess.org"]' in pgn
    assert '[ECO "B20"]' in pgn
    assert f"{{ {SETTINGS} }}" in flat(pgn)
    assert "1. e4 c5 { White wins 48%, draws 4%, Black wins 48% }" in flat(pgn)


async def test_continuations_follow_the_line_most_played_first() -> None:
    answer = explorer_answer(
        moves=[
            next_move("b1c3", "Nc3", 50, 4, 46),  # 100 games
            next_move("g1f3", "Nf3", 500, 40, 460),  # 1000
            next_move("f1c4", "Bc4", 50, 4, 46),  # 100, after Nc3: stable
            next_move("d2d3", "d3", 25, 0, 25),  # 50
        ]
    )
    text = flat(await fetch_one("Sicilian_Defense", Explorer({"e2e4,c7c5": answer})))
    # 1250 games across the moves listed: shares of 80, 8, 8 and 4 per cent.
    assert (
        "2. Nf3 { Sicilian Defense. Popularity 80%. "
        "White wins 50%, draws 4%, Black wins 46%. }"
    ) in text
    assert "( 2. Nc3 { Closed. Popularity 8%." in text
    assert "( 2. Bc4 { Bowdler Attack. Popularity 8%." in text
    # No named opening after 2. d3, so it goes unnamed, as on the page.
    assert (
        "( 2. d3 { Popularity 4%. White wins 50%, draws 0%, Black wins 50%. } )" in text
    )
    assert text.index("2. Nc3") < text.index("2. Bc4")


def test_percentages_round_half_up_as_the_page_does() -> None:
    assert _round(2.5) == 3
    assert _round(0.5) == 1
    assert _round(48.49) == 48


def test_a_result_is_worked_out_in_single_precision_as_lila_does() -> None:
    # White's exact share is 48.4999983%, which lila's Float makes 48.5, and
    # its page says 49%.
    stats = {"white": 145_516, "draws": 12_000, "black": 142_517}
    assert results(stats) == "White wins 49%, draws 4%, Black wins 48%"


async def test_a_position_the_explorer_does_not_know_says_so() -> None:
    gone = FetchError("explorer failed: 404", status=404)
    pgn = await fetch_one("Sicilian_Defense", Explorer({"e2e4,c7c5": gone}))
    assert f"1. e4 c5 {{ {NOT_IN_DATABASE} }}" in flat(pgn)


def test_a_position_with_no_games_has_no_result_comment() -> None:
    sicilian = table().pages["Sicilian_Defense"]
    game = opening_game(
        sicilian, table(), json.loads(explorer_answer(totals=(0, 0, 0)))
    )
    last = game.root.children[0].children[0]
    assert last.post_comment == ""
    assert last.children == []


async def test_example_games_follow_top_then_recent_once_each() -> None:
    answer = explorer_answer(top=("top1", "both"), recent=("both", "new1", "gone"))
    fetcher = Explorer({"e2e4,c7c5": answer}, export=exported("new1", "both", "top1"))
    pgn = await fetch_one("Sicilian_Defense", fetcher)
    ((url, headers, body),) = fetcher.posts
    assert url == GAMES_EXPORT
    assert body == "top1,both,new1,gone"
    assert "Authorization" not in headers  # the games are public
    order = [pgn.index(f'[GameId "{i}"]') for i in ("top1", "both", "new1")]
    assert order == sorted(order)
    # Lichess left one out; so does the file.
    assert "gone" not in pgn


async def test_no_export_is_asked_for_a_page_without_example_games() -> None:
    fetcher = Explorer()
    await fetch_one("Sicilian_Defense", fetcher)
    assert fetcher.posts == []


def test_exported_games_are_kept_as_exported() -> None:
    text = exported("abc12345")
    assert order_games(text, ["abc12345"]) == [text.strip()]


# The description.


def test_the_openings_read_pages_through_a_window() -> None:
    assert OpeningsExtractor.reads_pages is True
    assert OpeningsExtractor.needs_browser is False


async def test_the_description_follows_the_settings_in_the_game_comment() -> None:
    page = opening_page(
        "<p>Black answers from the side.</p>\n<hr>\n<p>An asymmetrical game.</p>"
    )
    fetcher = Explorer(pages={"Sicilian_Defense": page})
    pgn = await fetch_one("Sicilian_Defense", fetcher)
    assert fetcher.read == ["https://lichess.org/opening/Sicilian_Defense"]
    assert (
        f"{{ {SETTINGS}\n\nBlack answers from the side.\n\n"
        "An asymmetrical game. }\n1. e4 c5"
    ) in pgn


async def test_a_page_with_no_description_leaves_the_settings_alone() -> None:
    pgn = await fetch_one("Sicilian_Defense", Explorer())
    assert f"{{ {SETTINGS} }} 1. e4 c5" in flat(pgn)


async def test_a_page_that_cannot_be_read_fails_the_opening() -> None:
    # Nothing is written, so the next run asks again rather than skipping a
    # file that lacks its description.
    blocked = Blocked("https://lichess.org/opening/Sicilian_Defense: bot challenge")
    fetcher = Explorer(pages={"Sicilian_Defense": blocked})
    with pytest.raises(Blocked):
        await fetch_one("Sicilian_Defense", fetcher)
    assert fetcher.explorer_queries()[-1]["play"] == [""]  # only discovery's


async def test_a_page_without_its_wiki_block_fails_the_opening() -> None:
    fetcher = Explorer(pages={"Sicilian_Defense": "<html><p>Changed</p></html>"})
    with pytest.raises(FetchError, match="no description block"):
        await fetch_one("Sicilian_Defense", fetcher)


# lila's limit on opening pages.


async def test_a_page_refused_for_the_limit_is_waited_out_for_its_window(
    waits: list[float],
) -> None:
    class LimitedOnce(Explorer):
        refused = False

        async def get_page(self, url: str) -> str:
            if not self.refused:
                self.refused = True
                self.read.append(url)
                raise failure(url, 429, "<title>Too many requests.</title>")
            return await super().get_page(url)

    fetcher = LimitedOnce()
    await fetch_one("Sicilian_Defense", fetcher)
    assert waits == [openings.PAGE_LIMIT_WINDOW]
    assert fetcher.read == ["https://lichess.org/opening/Sicilian_Defense"] * 2
