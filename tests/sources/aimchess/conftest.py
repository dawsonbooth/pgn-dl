"""Fakes for the aimchess tests.

Payloads are invented in the live format, never captured: much of it is paid
content. The PGNs keep the live data's quirks: HTML comments, ChessBase
placeholder headers, ``%end%``, a command broken across lines.
"""

from __future__ import annotations

from collections.abc import Mapping
from json import dumps
from typing import Any

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.aimchess import auth

TOKEN = "DUMMY-TOKEN-DO-NOT-LEAK"

API = "https://aimchess.com/api"
LISTING_URL = f"{API}/explore/lessons/"
CHECKMATES_URL = f"{API}/modules/checkmate_flashcards/"


def lesson_url(lesson_id: int | str) -> str:
    return f"{API}/explore/lessons/{lesson_id}/"


#: White to move, so the step is shown from Black's side.
TUTORIAL = """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "Tutorial"]
[Result "*"]
[SetUp "1"]
[FEN "8/8/8/8/8/1k6/8/1K5R w - - 0 1"]
[PlyCount "2"]
[SourceVersionDate "2024.01.01"]

{[%evp 0,2,900,910,920]} 1. Rh3+ {<p>The rook cuts the king off along the
third rank.</p>} Kb4 {<p>The king has to step back.</p>} *"""

#: Black to move, so the student plays White.
EXERCISE = """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "1-0"]
[SetUp "1"]
[FEN "r1bqkbnr/pppp1ppp/2n5/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3"]
[PlyCount "2"]

3... Nf6 {[%cal Gh5f7,
Gc4f7] <p>Black ignores the threat.</p>} 4. Qxf7# {%end% <p>Well done! That is
mate.</p>} ({<p>Instead:</p>} 4. Qxe5+ {<p>A check, but Black covers it.</p>}
Qe7) 1-0"""


def entry(
    lesson_id: int,
    name: str,
    topic: str,
    section: str | None = None,
    *,
    status: str = "published",
) -> dict[str, Any]:
    return {
        "id": lesson_id,
        "name": name,
        "topic": topic,
        "section": section,
        "status": status,
        "new": False,
        "progress": "not started",
    }


#: Interleaved across topics, as the live listing is.
LESSONS = (
    entry(24, "Doubled Pawns", "Middlegame", "Strategic concepts"),
    entry(1, "Rook Endings", "Endgame", "Practical endgames"),
    entry(17, "Greek Gift", "Middlegame", "Attacking concepts"),
    entry(2, "Queen Endings", "Endgame", "Practical endgames"),
    entry(40, "Center Control", "Opening"),
)

TOPICS = (
    {"name": "Endgame", "sections": ["Practical endgames"]},
    {"name": "Middlegame", "sections": ["Strategic concepts", "Attacking concepts"]},
    {"name": "Opening", "sections": []},
)


def listing_body(
    lessons: tuple[dict[str, Any], ...] = LESSONS,
    topics: tuple[dict[str, Any], ...] = TOPICS,
) -> dict[str, Any]:
    return {
        "topics": list(topics),
        "lessons": list(lessons),
        "limits": {"daily_limit_total": None, "daily_limit_available": None},
    }


def lesson_body(
    base: dict[str, Any], steps: tuple[tuple[str, str], ...] | None = None
) -> dict[str, Any]:
    """A lesson as the POST returns it: the listing entry plus its steps."""
    if steps is None:
        steps = (("tutorial", TUTORIAL), ("exercise", EXERCISE))
    return base | {
        "steps_data": [{"step_type": kind, "pgn": pgn} for kind, pgn in steps]
    }


#: Two Checkmate Patterns flashcards: one mate in one, one longer line.
CARDS = [
    {
        "id": 0,
        "fen": "6k1/4N1p1/8/8/8/4R3/8/6K1 b - - 0 1",
        "firstMove": "Kh7",
        "solution": "Rh3#",
        "title": "Anastasia's Mate",
    },
    {
        "id": 1,
        "fen": "r2qkbnr/ppp2ppp/2np4/4N2b/2B1P3/2N4P/PPPP1PP1/R1BQK2R b KQkq - 0 1",
        "firstMove": "Bxd1",
        "solution": "Bxf7+ Ke7 Nd5#",
        "title": "Legal's Mate",
    },
]


class FakeFetcher:
    """Answers the API's GETs and the lesson POSTs, recording both.

    Every request must carry the token, and a lesson is opened with an empty
    POST, as the app opens one.
    """

    def __init__(
        self,
        listing: Mapping[str, Any] | None = None,
        lessons: Mapping[str, Any] | None = None,
        *,
        statuses: Mapping[str, int] | None = None,
    ) -> None:
        self.gets_by_url = {
            LISTING_URL: listing_body() if listing is None else listing,
            CHECKMATES_URL: {"problems": CARDS},
        }
        self.lessons = dict(lessons or {})
        self.statuses = dict(statuses or {})
        self.gets: list[str] = []
        self.posts: list[str] = []

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.gets.append(url)
        assert headers.get("Authorization") == f"JWT {TOKEN}", "must send the token"
        if url not in self.gets_by_url:
            raise FetchError(f"{url} failed: 404 (JSON body)", status=404)
        return dumps(self.gets_by_url[url])

    async def post_text(self, url, *, headers, body):  # pragma: no cover
        raise AssertionError("no text POSTs in these tests")

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        self.posts.append(url)
        assert headers.get("Authorization") == f"JWT {TOKEN}", "must send the token"
        assert json is None, "a lesson is opened with an empty POST"
        lesson_id = url.rstrip("/").rsplit("/", 1)[-1]
        if (status := self.statuses.get(lesson_id)) is not None:
            raise FetchError(f"{url} failed: {status} (JSON body)", status=status)
        if lesson_id not in self.lessons:
            raise FetchError(f"{url} failed: 404 (JSON body)", status=404)
        return self.lessons[lesson_id]


@pytest.fixture
def stub_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read the credential file."""
    monkeypatch.setattr(auth, "load_token", lambda: TOKEN)


# The site's JavaScript, for the quiz and motifs.

ASSETS = "https://aimchess.com/static/aimchess-assets"

#: An app page: the entry scripts, plus a third-party one to skip.
APP_SHELL = (
    '<script async src="https://www.googletagmanager.com/gtag/js"></script>'
    '<script src="/static/aimchess-assets/vendor.bundle.js"></script>'
    '<script src="/static/aimchess-assets/app.bundle.js"></script>'
)

#: A quiz puzzle's PGN as a JavaScript string: escaped newlines and quote.
QUIZ_PGN = (
    r"""'\uFEFF[Event "?"]\n[White "Opening"]\n[Black "?"]\n[Result "*"]\n"""
    r"""[SetUp "1"]\n[FEN "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b"""
    r""" KQkq - 0 2"]\n\n2... Nc6 {Develop, and don\'t block the d-pawn.} (2..."""
    r""" Bd6 {This bishop blocks the d7\npawn.}) *'"""
)

MOTIF_PGN = (
    r"""'[Event "?"]\n[White "?"]\n[Black "?"]\n[Result "*"]\n[SetUp "1"]\n"""
    r"""[FEN "8/7b/8/4k3/4p1N1/2K1P3/5P2/8 b - - 0 1"]\n\n1... Kd5 {[%csl Gd5,Gh7]"""
    r""" A mistake.} 2. Nf6+ {[%cal Gf6d5,Gf6h7] A fork.} *'"""
)


def quiz_puzzle(difficulty: int, category: str) -> str:
    return (
        f'{{difficulty:{difficulty},category:"{category}",fen:"x",'
        'moves:{best:{san:"Nc6",description:"Good."},bad:{san:"Bd6",description:""}},'
        f"pgn:{QUIZ_PGN}}}"
    )


APP_SCRIPT = (
    'n.p="/static/",n.u=e=>"aimchess-assets/"+e+"."'
    '+({12:"aaaa1111",34:"bbbb2222"})[e]+".bundle.js";'
    f'var m={{fork:{{name:"Fork",desc:"Two attacks at once.",pgn:{MOTIF_PGN}}},'
    f'doubleCheck:{{name:" Doble",desc:"Check from two pieces.",pgn:{MOTIF_PGN}}}}};'
)

_PUZZLES = ",".join(
    [quiz_puzzle(2, "opening"), quiz_puzzle(1, "opening"), quiz_puzzle(1, "tactics")]
)
QUIZ_CHUNK = f"var i={{year:2021,month:9,puzzles:[{_PUZZLES}]}};"


def site_pages(**overrides: str) -> dict[str, str]:
    """The app pages and scripts, by URL."""
    pages = {
        "https://aimchess.com/public/quiz": APP_SHELL,
        "https://aimchess.com/motif/fork": APP_SHELL,
        f"{ASSETS}/vendor.bundle.js": "var v=1;",
        f"{ASSETS}/app.bundle.js": APP_SCRIPT,
        f"{ASSETS}/12.aaaa1111.bundle.js": "var c=1;",
        f"{ASSETS}/34.bbbb2222.bundle.js": QUIZ_CHUNK,
    }
    return pages | overrides


class SiteFetcher:
    """Serves the site's pages and scripts by URL. They need no token."""

    def __init__(self, pages: Mapping[str, str] | None = None) -> None:
        self.pages = site_pages() if pages is None else dict(pages)
        self.gets: list[str] = []

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.gets.append(url)
        if url not in self.pages:
            raise FetchError(f"{url} failed: 404 (HTML page)", status=404)
        return self.pages[url]

    async def post_text(self, url, *, headers, body):  # pragma: no cover
        raise AssertionError("no text POSTs in these tests")

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("the site's scripts are only read")
