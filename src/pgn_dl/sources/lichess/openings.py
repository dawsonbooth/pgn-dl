"""Lichess's opening pages (lichess.org/opening).

An opening page shows a named opening's line, a description where Lichess has
written one, how Lichess games went from it, the moves most played next
(Popular continuations), and a few games that reached it (Example games).
Everything but the description is computed from things Lichess publishes, and
this module computes it the way lila does (``modules/opening``):

- The named openings are scalachess's opening table, at the version lila pins
  (``project/Dependencies.scala``), read from GitHub. A name has one page, and
  its line is the shortest of that name's (``OpeningDb.shortestLines``).
- The results, continuations and games come from the explorer of Lichess
  games, asked exactly what lila asks for a page under the default settings:
  since 2017, bullet to correspondence, ratings 1000 to 2500, with the
  explorer's own defaults for how many moves and games come back. It needs
  the token `pgn-dl login lichess` saves.
- The games' PGN is Lichess's export by id, with clocks and without
  evaluations or opening tags, which is how the page embeds them.

The description is Lichess's opening wiki, which has no API, so it is read
from the page itself, through a visible Chrome window, signed out
(``wiki.py``). Most openings have none.

Each opening is one file. Its first game is the line, with the settings and
the description as the game comment, the results as a comment on its last
move, and the popular continuations as the moves after it: the most played as
the next move, the rest as its variations, each with its popularity and
results. Then come the example games, as Lichess exports them.
Files are named by the page's key in a directory per family and are not
numbered: the table is alphabetical, and numbering it would rename every file
after any opening Lichess adds. The openings page itself, which lists the
first moves the same way, is ``index.pgn`` beside them.
"""

from __future__ import annotations

import io
import math
import re
import struct
import unicodedata
from dataclasses import dataclass
from functools import cached_property
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode

import chess
import chess.pgn

from ...core.extractor import Extractor, WholeSection
from ...core.fetch import Blocked, Fetcher, FetchError, UnknownTarget
from ...core.models import Collection, GameItem, ItemRef, MoveNode
from ...core.paths import slugify
from ...core.pgn import game_to_pgn
from . import auth, wiki
from .client import EXPLORER, ROOT, SITE, Client

COLLECTION_NAME = "Chess openings"
PAGE = f"{ROOT}/opening"

LILA_DEPENDENCIES = "https://raw.githubusercontent.com/lichess-org/lila/master/project/Dependencies.scala"
SCALACHESS = "https://raw.githubusercontent.com/lichess-org/scalachess"
TABLE_PARTS = "ABCDE"

#: What lila asks the explorer for a page under the default settings
#: (``OpeningExplorer.stats``, ``OpeningConfig.default``, ``OpeningQuery``).
#: It sends no ``moves``, ``topGames`` or ``recentGames``, so neither does this.
EXPLORER_QUERY = {
    "since": "2017-01",
    "ratings": "1000,1200,1400,1600,1800,2000,2200,2500",
    "speeds": "bullet,blitz,rapid,classical,correspondence",
    "history": "yes",
}
#: How the page sums those settings up, and so how the file does.
SETTINGS = "Lichess games, speed Bullet to Correspondence, rating 1000 to 2500."
NOT_IN_DATABASE = "This opening is not in our database."
#: The example games as the page embeds them.
GAMES_EXPORT = f"{ROOT}/api/games/export/_ids?clocks=true&evals=false&opening=false"

#: How long a page refused for lila's limit on opening pages is waited out: the
#: limit's whole window. lila allows each address 60 credits per 10 minutes for
#: them (``controllers/Opening.scala``), and a signed-out page costs 2.
PAGE_LIMIT_WINDOW = 600.0

_VERSION = re.compile(r'object chess\s*:\s*val version\s*=\s*"([^"]+)"')
_STRING = r'"((?:[^"\\]|\\.)*)"'
_ROW = re.compile(rf"Opening\({_STRING}, {_STRING}, {_STRING}, {_STRING}, {_STRING}\)")
_SCALA_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)")
_ACCENT = re.compile("[̀-ͯ]")
_SPACES = re.compile(r"\s+", re.ASCII)
#: Java's ``\w`` is ASCII unless asked otherwise, so a letter with no
#: decomposition ("ø") drops out of the key, as it does on Lichess.
_BAD_KEY_CHARS = re.compile(r"[^\w\-]+", re.ASCII)


def name_to_key(name: str) -> str:
    """A name's page key, as scalachess makes it (``Opening.nameToKey``)."""
    decomposed = _ACCENT.sub("", unicodedata.normalize("NFD", name))
    return _BAD_KEY_CHARS.sub("", _SPACES.sub("_", decomposed))


def _scala_unescape(text: str) -> str:
    def one(m: re.Match[str]) -> str:
        code = m.group(1)
        return chr(int(code[1:], 16)) if len(code) > 1 else code

    return _SCALA_ESCAPE.sub(one, text)


@dataclass(frozen=True)
class Opening:
    eco: str
    name: str
    fen: str  # board, turn, castling, en passant: EPD
    uci: str  # space-separated, from the initial position
    pgn: str

    @property
    def key(self) -> str:
        return name_to_key(self.name)

    @property
    def family(self) -> str:
        return self.name.split(":", 1)[0]


def sections_of(name: str) -> list[str]:
    """A name's family and variations (``NameSection.sectionsOf``)."""
    family, colon, rest = name.partition(":")
    if not colon:
        return [name]
    variations = [v.strip() for v in rest.split(",")]
    while variations and not variations[-1]:  # Java's split drops trailing empties
        variations.pop()
    return [family, *variations]


def section_name(prev: str, after: str) -> str:
    """The part of ``after`` that differs from ``prev``, as lila shortens names.

    The first section after the ones the two names share, or, when the next
    name only drops sections, its last: "Sicilian Defense" to "Sicilian
    Defense: Closed" is "Closed".
    """
    ours, theirs = sections_of(prev), sections_of(after)
    width = max(len(ours), len(theirs))
    pairs = zip(
        ours + [""] * (width - len(ours)), theirs + [""] * (width - len(theirs))
    )
    first = next((b for a, b in pairs if a != b), "")
    return first or theirs[-1]


def variation_name(prev: Opening | None, after: Opening | None) -> str | None:
    """What a continuation is called on the page (``NameSection.variationName``).

    With no named opening before the move, the next opening's family; with
    none after it, nothing.
    """
    if after is None:
        return None
    if prev is None:
        return after.family
    return section_name(prev.name, after.name)


@dataclass(frozen=True)
class Table:
    """scalachess's opening table: every row, by position and by page."""

    rows: tuple[Opening, ...]

    @cached_property
    def by_fen(self) -> dict[str, Opening]:
        return {row.fen: row for row in self.rows}  # the last of a position wins

    @cached_property
    def pages(self) -> dict[str, Opening]:
        """One opening per key, the shortest line (``OpeningDb.shortestLines``)."""
        shortest: dict[str, Opening] = {}
        for row in self.rows:
            prev = shortest.get(row.key)
            if prev is None or len(prev.uci) >= len(row.uci):
                shortest[row.key] = row
        return shortest


def parse_table(parts: list[str]) -> Table:
    rows = tuple(
        Opening(*(_scala_unescape(field) for field in m.groups()))
        for part in parts
        for m in _ROW.finditer(part)
    )
    if not rows:
        raise FetchError("found no openings in scalachess's opening table")
    return Table(rows)


async def read_table(client: Client) -> Table:
    """The opening table at the scalachess version lila builds with."""
    m = _VERSION.search(await client.text(LILA_DEPENDENCIES))
    if m is None:
        raise FetchError("found no scalachess version in lila's dependencies")
    path = f"{SCALACHESS}/{m.group(1)}/core/src/main/scala/opening"
    parts = [await client.text(f"{path}/OpeningDbPart{p}.scala") for p in TABLE_PARTS]
    return parse_table(parts)


async def explore(client: Client, uci: str) -> dict[str, Any] | None:
    """The explorer's answer for the position after ``uci``; None if it has none."""
    query = {"play": ",".join(uci.split()), **EXPLORER_QUERY}
    try:
        data = await client.json(f"{EXPLORER}/lichess?{urlencode(query)}", auth=True)
    except Blocked:
        raise
    except FetchError as exc:
        if exc.status == 401:
            raise FetchError(
                "Lichess refused the saved token; run: pgn-dl login lichess",
                status=401,
            ) from None
        if exc.status == 404:
            return None  # what the page calls "not in our database"
        raise
    if not isinstance(data, dict):
        raise FetchError("the explorer's answer is not an object")
    return data


def _round(x: float) -> int:
    """Java's ``Math.round``, which lila uses for the page: halves go up."""
    return math.floor(x + 0.5)


def _single(x: float) -> float:
    """``x`` rounded to single precision, a Java ``Float``."""
    return struct.unpack("f", struct.pack("f", x))[0]


def _share(count: int, total: int) -> float:
    """A result's percentage as lila works it out, in single precision
    (``ResultCounts.percentOf``), which can round to another whole number
    than the exact share does."""
    return _single(_single(_single(count) * 100) / _single(total))


def results(stats: dict[str, Any]) -> str:
    """A result bar in words: "White wins 48%, draws 4%, Black wins 48%"."""
    white, draws, black = (int(stats[k]) for k in ("white", "draws", "black"))
    total = white + draws + black
    return (
        f"White wins {_round(_share(white, total))}%, "
        f"draws {_round(_share(draws, total))}%, "
        f"Black wins {_round(_share(black, total))}%"
    )


def _total(stats: dict[str, Any]) -> int:
    return int(stats["white"]) + int(stats["draws"]) + int(stats["black"])


def opening_game(
    opening: Opening,
    table: Table,
    explored: dict[str, Any] | None,
    description: str | None = None,
) -> GameItem:
    """The page's line, description, results and continuations, as one game.

    The settings come first in the comment, as the page's settings bar comes
    first, then the description.
    """
    board = chess.Board()
    root = MoveNode(san=None)
    node = root
    for uci in opening.uci.split():
        move = board.parse_uci(uci)
        child = MoveNode(san=board.san(move))
        node.children.append(child)
        node = child
        board.push(move)
    if explored is None:
        node.post_comment = NOT_IN_DATABASE
    else:
        if _total(explored) > 0:
            node.post_comment = results(explored)
        _continue(node, board, table, explored, table.by_fen.get(opening.fen))
    return GameItem(
        headers={
            "Event": opening.name,
            "Site": SITE,
            "ECO": opening.eco,
            "Opening": opening.name,
        },
        comment=f"{SETTINGS}\n\n{description}" if description else SETTINGS,
        root=root,
    )


def index_game(table: Table, explored: dict[str, Any] | None) -> GameItem:
    """The openings page itself: the first moves, named by family as it names them.

    The page shows only its continuations: no result bar, no example games.
    """
    root = MoveNode(san=None)
    comment = SETTINGS
    if explored is None:
        comment = f"{SETTINGS}\n\n{NOT_IN_DATABASE}"
    else:
        _continue(root, chess.Board(), table, explored, None)
    return GameItem(
        headers={"Event": COLLECTION_NAME, "Site": SITE}, comment=comment, root=root
    )


def _continue(
    node: MoveNode,
    board: chess.Board,
    table: Table,
    explored: dict[str, Any],
    exact: Opening | None,
) -> None:
    """Add the page's popular continuations after ``node``, most played first."""
    by_fen = table.by_fen
    moves = list(explored.get("moves") or [])
    moves_total = sum(_total(m) for m in moves)
    # Python's sort is stable, as Scala's sortBy is.
    for move in sorted(moves, key=lambda m: -_total(m)):
        after = board.copy()
        after.push(after.parse_uci(move["uci"]))
        parts = []
        if name := variation_name(exact, by_fen.get(after.epd())):
            parts.append(f"{name}.")
        if moves_total:
            parts.append(f"Popularity {_round(_total(move) * 100 / moves_total)}%.")
        if _total(move):
            parts.append(f"{results(move)}.")
        node.children.append(MoveNode(san=move["san"], post_comment=" ".join(parts)))


def example_ids(explored: dict[str, Any] | None) -> list[str]:
    """The page's example games: the top games, then the recent ones, once each."""
    if explored is None:
        return []
    games = [*(explored.get("topGames") or []), *(explored.get("recentGames") or [])]
    return list(dict.fromkeys(str(g["id"]) for g in games))


def order_games(pgn: str, ids: list[str]) -> list[str]:
    """The exported games' texts in ``ids``' order; ones Lichess left out are gone.

    Only the headers are read: the text is written as exported.
    """
    stream = io.StringIO(pgn)
    found: dict[str, str] = {}
    while True:
        start = stream.tell()
        headers = chess.pgn.read_headers(stream)
        if headers is None:
            break
        game_id = headers.get("GameId") or headers.get("Site", "").rsplit("/", 1)[-1]
        found[game_id] = pgn[start : stream.tell()].strip()
    return [found[i] for i in ids if i in found]


_TARGET = re.compile(
    r"^(?:https?://(?:www\.)?lichess\.org/opening(?:/(?P<key>[^/?#]+)(?P<line>/[^?#]*)?)?/?"
    r"|(?P<bare>[\w'%-]+))$"
)


@dataclass(frozen=True)
class Target:
    """The page a target names; ``key`` None is the openings page itself."""

    key: str | None = None
    line: tuple[str, ...] = ()  # the SAN moves a page URL carries, if any


def target_from(target: str) -> Target:
    """Read ``/opening``, ``/opening/<Key>`` or a bare key.

    A page URL may carry moves after the key, as the continuation links do
    (``/opening/Sicilian_Defense_Closed/e4_c5_Nc3``); which page that is
    depends on where the moves lead, and is settled once the table is read.
    """
    cleaned, _, query = target.strip().split("#", 1)[0].partition("?")
    if "q" in parse_qs(query):
        raise WholeSection(
            f"{target!r} is a search of the openings; give one opening's page or key"
        )
    m = _TARGET.match(cleaned)
    if m is None:
        raise ValueError(f"could not read a lichess opening from {target!r}")
    key = m.group("key") or m.group("bare")
    if key == "tree":
        raise WholeSection(
            f"{target!r} is the tree of every opening; give one opening's page or key"
        )
    if key is None:
        return Target()
    line = tuple(s for s in unquote(m.group("line") or "").strip("/").split("_") if s)
    return Target(unquote(key).replace("Defence", "Defense"), line)


def _reaches(opening: Opening, line: tuple[str, ...]) -> bool:
    """Whether ``line`` (SAN) leads to ``opening``'s position."""
    board = chess.Board()
    try:
        for san in line:
            board.push_san(san)
    except ValueError:
        return False
    return board.epd() == opening.fen


def find_page(pages: dict[str, Opening], key: str) -> Opening | None:
    """The page lila serves for ``key``: exact, or ignoring case."""
    if key in pages:
        return pages[key]
    lower = {k.lower(): v for k, v in pages.items()}
    return lower.get(name_to_key(key.lower()).lower())


@dataclass
class _Context:
    client: Client
    table: Table
    collection: Collection
    start: dict[str, Any] | None  # the explorer's answer for the first move


#: The openings page itself, written beside the openings.
INDEX = "index"


class OpeningsExtractor(Extractor[_Context]):
    """One opening's page, or the openings page (requires `pgn-dl login lichess`)."""

    key = "lichess:openings"
    reads_pages = True  # the descriptions; nothing else is read from a page
    url_patterns = (re.compile(r"^https?://(?:www\.)?lichess\.org/opening(?:[/?#]|$)"),)

    def __init__(self, target: str) -> None:
        super().__init__()
        self.target = target_from(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        client = Client(fetcher, token=auth.load_token())
        table = await read_table(client)
        # Asked before anything is listed, so a refused token stops the run
        # here with the login hint. It is also what the openings page shows.
        start = await explore(client, "")
        wanted = self.target
        if wanted.key is None:
            ref = ItemRef(id=INDEX, path=(INDEX,))
        elif (found := find_page(table.pages, wanted.key)) is None:
            raise UnknownTarget(f"lichess has no opening page {wanted.key!r}")
        elif wanted.line and not _reaches(found, wanted.line):
            raise UnknownTarget(
                f"{'_'.join(wanted.line)} does not reach {found.name}; only the "
                "named openings' own pages are downloaded"
            )
        else:
            ref = ItemRef(
                id=found.key,
                path=(slugify(name_to_key(found.family)), slugify(found.key)),
            )
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        return collection, [ref], _Context(client, table, collection, start)

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        if ref.id == INDEX:
            game = index_game(context.table, context.start)
            return GameItem(pgn=game_to_pgn(context.collection, game))
        opening = context.table.pages[ref.id]
        # Read first and failing the item if it fails: a file written without
        # its description would be skipped by every later run.
        url = f"{PAGE}/{opening.key}"
        page = await context.client.page(url, rate_limit_wait=PAGE_LIMIT_WINDOW)
        about = wiki.description(page, url)
        explored = await explore(context.client, opening.uci)
        games = [
            game_to_pgn(
                context.collection,
                opening_game(opening, context.table, explored, about),
            )
        ]
        if ids := example_ids(explored):
            exported = await context.client.post_text(
                GAMES_EXPORT, ",".join(ids), accept="application/x-chess-pgn"
            )
            games.extend(order_games(exported, ids))
        return GameItem(pgn="\n\n".join(games))
