"""Fakes for the lichess tests.

Every response is invented in the live format, never captured: a study export
as Lichess writes one (its headers, a game per chapter, two blank lines after
each), a study's JSON as lichess.org answers it, failures as the transport
raises them. Nothing here reaches the network,
and no test reads the token pgn-dl itself downloads with.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError, check_response
from pgn_dl.sources.lichess import auth
from pgn_dl.sources.lichess import client as client_module

ROOT = "https://lichess.org"
ASSETS = "https://lichess1.org/assets/compiled"
USER_AGENT = "pgn-dl (+https://github.com/dawsonbooth/pgn-dl)"

#: Invented: nothing here is a credential, and none of it may reach a message.
TOKEN = "DUMMY-TOKEN-DO-NOT-LEAK"

#: A response: the body text, or the exception to raise in its place.
Response = str | Exception
#: A request as :class:`FakeFetcher` records it: method, URL, headers, body.
Request = tuple[str, str, dict[str, str], str | None]


class FakeFetcher:
    """Answers each request from ``responses`` by its exact URL, recording it.

    A response is the body text, or an exception to raise instead, the way the
    transport raises a failed response (see :func:`failure`). A URL with no
    response fails the test. Every request is kept in ``requests`` as
    ``(method, url, headers, body)``, the body ``None`` for a GET. A page read
    through the window is method ``PAGE``.
    """

    def __init__(self, responses: Mapping[str, Response]) -> None:
        self.responses: dict[str, Response] = dict(responses)
        self.requests: list[Request] = []

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _, _ in self.requests]

    def _answer(
        self, method: str, url: str, headers: Mapping[str, str], body: str | None
    ) -> str:
        self.requests.append((method, url, dict(headers), body))
        if url not in self.responses:
            raise AssertionError(f"unexpected {method} {url}")
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        return self._answer("GET", url, headers, None)

    async def post_text(
        self, url: str, *, headers: Mapping[str, str], body: str
    ) -> str:
        return self._answer("POST", url, headers, body)

    async def get_page(self, url: str) -> str:
        """A page through the window: recorded with no headers, as none go."""
        return self._answer("PAGE", url, {}, None)

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("nothing sends JSON to Lichess")


def failure(url: str, status: int, body: str = "") -> FetchError:
    """What the transport raises for a ``status`` response to ``url``.

    A 429 is a :class:`~pgn_dl.core.fetch.Blocked`, as is a bot challenge's
    page under any status; anything else is a plain failure with its status.
    """
    try:
        check_response(url, status, {}, body)
    except FetchError as exc:
        return exc
    raise AssertionError(f"a {status} is not a failure")


#: The page a bot challenge serves in place of the answer.
CHALLENGE_PAGE = "<!DOCTYPE html><html><title>Just a moment...</title></html>"


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read the token pgn-dl itself downloads with."""
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


def save_token(token: str = TOKEN) -> None:
    """A token as an earlier ``pgn-dl login lichess`` left it on disk."""
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")


def fake_waits(
    monkeypatch: pytest.MonkeyPatch, then: Callable[[], None] | None = None
) -> list[float]:
    """Every wait the client asks for, in seconds, and none of them real: the
    client's clock moves on by each wait instead, and ``then`` runs after it."""
    asked: list[float] = []
    now = 0.0

    def clock() -> float:
        return now

    async def wait(seconds: float) -> None:
        nonlocal now
        asked.append(seconds)
        now += seconds
        if then is not None:
            then()

    monkeypatch.setattr(client_module.asyncio, "sleep", wait)
    monkeypatch.setattr(client_module, "monotonic", clock)
    return asked


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every wait the client asks for (see :func:`fake_waits`)."""
    return fake_waits(monkeypatch)


def games(text: str) -> list[chess.pgn.Game]:
    """Every game in a PGN text, each read back without errors."""
    buf, found = io.StringIO(text), []
    while (game := chess.pgn.read_game(buf)) is not None:
        assert not game.errors, game.errors
        found.append(game)
    return found


# Studies.


def study_url(study: str) -> str:
    """Where the API exports a study."""
    return f"{ROOT}/api/study/{study}.pgn"


STUDY = "h7Sac1fz"
STUDY_NAME = "Sacrifices on h7"


def chapter(
    chapter_id: str,
    name: str,
    moves: str,
    *,
    fen: str | None = None,
    study: str = STUDY,
    study_name: str = STUDY_NAME,
    **headers: str | None,
) -> str:
    """One chapter as Lichess's study export writes it: headers, then moves.

    ``headers`` adds to or replaces the export's own; ``None`` leaves one out.
    """
    tags: dict[str, str | None] = {
        "Event": f"{study_name}: {name}",
        "Date": "2026.01.02",
        "Result": "*",
        "Variant": "Standard",
        "ECO": "?",
        "Opening": "?",
        "StudyName": study_name,
        "ChapterName": name,
        "ChapterURL": f"{ROOT}/study/{study}/{chapter_id}",
        "Annotator": f"{ROOT}/@/someone",
    }
    if fen is not None:
        tags |= {"FEN": fen, "SetUp": "1"}
    tags |= {"UTCDate": "2026.01.02", "UTCTime": "10:00:00", "Orientation": "white"}
    tags |= headers
    lines = [f'[{key} "{value}"]' for key, value in tags.items() if value is not None]
    return "\n".join(lines) + "\n\n" + moves


def study_export(*chapters: str) -> str:
    """Chapters as one export, each followed by two blank lines as Lichess ends it."""
    return "".join(f"{text}\n\n\n" for text in chapters)


#: Black's rook stands on f8, so the ``(1... Kf8)`` sideline cannot be played:
#: Lichess's data has such moves (Practice's Greek Gift introduction is one).
SACRIFICE_FEN = "5rk1/6pp/8/8/8/3B4/8/6K1 w - - 0 1"
SACRIFICE_ID = "kT4wQ9zR"
SACRIFICE = chapter(
    SACRIFICE_ID,
    "The bishop takes on h7",
    "{ [%cal Gd3h7] } 1. Bxh7+ Kxh7 { The king is out in the open, as in Réti's game. } "
    "(1... Kf8) *",
    fen=SACRIFICE_FEN,
)
DEFENCE_ID = "Pq7XcV2m"
DEFENCE = chapter(
    DEFENCE_ID,
    "The knight on f6 defends",
    "1. e4 $1 { [%csl Ge4] } 1... Nf6 2. e5 *",
)
MATE_ID = "Zz81LmNo"
MATE = chapter(
    MATE_ID,
    "Queen and knight mate",
    "1. Qh7# *",
    fen="5rk1/5pp1/8/6NQ/8/8/8/6K1 w - - 0 1",
)


# A study's JSON, as lichess.org answers a request for it.


def study_json_url() -> str:
    """Where the study's JSON lists its chapters."""
    return f"{ROOT}/study/{STUDY}?chapters=true"


def chapter_json_url(chapter_id: str, study: str = STUDY) -> str:
    """Where a chapter's JSON is."""
    return f"{ROOT}/study/{study}/{chapter_id}"


def study_json(
    shown: str,
    *,
    study: str = STUDY,
    name: str = STUDY_NAME,
    chapters: Sequence[tuple[str, str]] | None = None,
    description: str | None = None,
    study_description: str | None = None,
    shows_description: bool = False,
    tree: object = None,
) -> str:
    """A study's JSON for the chapter ``shown``, reduced to what is read.

    ``chapters`` is the list a request for the whole study gets; ``tree`` the
    chapter's moves as ``treeParts``; the descriptions are the chapter's own
    and the study's, the second shown only with ``shows_description``.
    """
    found: dict = {
        "id": study,
        "name": name,
        "visibility": "public",
        "settings": {"cloneable": "owner", "shareable": "owner"},
        "features": {"cloneable": False, "shareable": False, "chat": True},
        "chapter": {"id": shown, "name": "", "ownerId": "someone"},
    }
    if description is not None:
        found["chapter"]["description"] = description
    if study_description is not None:
        found["description"] = study_description
    if shows_description:
        found["features"]["description"] = True
    if chapters is not None:
        found["chapters"] = [{"id": i, "name": n} for i, n in chapters]
    parts = [{"ply": 0, "fen": chess.STARTING_FEN}] if tree is None else tree
    return json.dumps({"study": found, "analysis": {"treeParts": parts}})
