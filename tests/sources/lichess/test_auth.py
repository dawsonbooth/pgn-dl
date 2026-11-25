"""Lichess's sign-in: OAuth with PKCE, the check, the save.

Lichess is never reached: its token and account endpoints are an
``httpx.MockTransport``, and the user's browser is a fake that follows the
authorization page's redirect back to pgn-dl's listener over this machine's
loopback, as a real one would after Authorize. Storage invariants are the
credential store's, tested in tests/core/test_credentials.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import string
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess import auth

from .conftest import TOKEN, USER_AGENT, save_token

ACCOUNT = "https://lichess.org/api/account"


def test_no_token_is_saved_until_login() -> None:
    assert auth.saved_token() is None
    with pytest.raises(FetchError, match="login lichess"):
        auth.load_token()


def test_a_saved_token_is_read_back() -> None:
    save_token()
    assert auth.saved_token() == TOKEN
    assert auth.load_token() == TOKEN


@pytest.mark.parametrize(
    "saved",
    [
        "not json",
        '{"jwt": "wrong-field"}',
        '{"token": ""}',
        # One a hand-made file might hold: no header can carry it, and the
        # error httpx raises for it would quote it whole.
        '{"token": "lio_SECRET\\u00a0"}',
        '{"token": "lio_SECRET and more"}',
    ],
)
def test_a_credentials_file_that_cannot_be_read_is_refused_not_ignored(
    saved: str,
) -> None:
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(saved, encoding="utf-8")
    with pytest.raises(FetchError, match="login lichess") as exc:
        auth.saved_token()
    assert "wrong-field" not in str(exc.value)
    assert "SECRET" not in str(exc.value)


def answer(
    response: httpx.Response | Exception,
) -> Callable[[httpx.Request], httpx.Response]:
    """An account endpoint giving ``response``, checking how it is asked."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, str(request.url)) == ("GET", ACCOUNT)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["User-Agent"] == USER_AGENT
        if isinstance(response, Exception):
            raise response
        return response

    return handler


async def test_the_account_check_names_the_token_s_user(
    monkeypatch: pytest.MonkeyPatch, mock_httpx_client: Callable[..., Any]
) -> None:
    account = httpx.Response(200, json={"id": "someone", "username": "SomeOne"})
    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(answer(account)))
    assert await auth.account_name(TOKEN) == "SomeOne"


# Every body echoes the token, as an error page reflecting the request might.
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(401, text=f"bad token {TOKEN}"),
            "Lichess refused that token; nothing was saved",
        ),
        (
            httpx.Response(500, text=f"error for {TOKEN}"),
            "checking the token failed: 500",
        ),
        (
            httpx.Response(200, text=f"<html>{TOKEN}</html>"),
            "checking the token returned a malformed response",
        ),
        (
            httpx.Response(200, json={"id": TOKEN}),
            "checking the token returned a malformed response",
        ),
        (
            httpx.Response(200, json=[TOKEN]),
            "checking the token returned a malformed response",
        ),
        (
            httpx.Response(200, json={"username": ""}),
            "checking the token returned a malformed response",
        ),
        (
            httpx.ConnectTimeout(f"timed out sending {TOKEN}"),
            "could not reach Lichess to check the token: ConnectTimeout",
        ),
    ],
)
async def test_a_failed_check_says_why_and_never_shows_the_token(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
    response: httpx.Response | Exception,
    message: str,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(answer(response)))
    with pytest.raises(FetchError) as exc:
        await auth.account_name(TOKEN)
    assert str(exc.value) == message
    # Nor would a traceback show it: nothing that carried the request is chained.
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__


# Signing in: authorize in the browser, come back with a code.

#: Invented, like TOKEN: none of these may reach a message.
CODE = "liu_DUMMY-CODE-DO-NOT-LEAK"
#: A year, as Lichess issues its OAuth tokens.
LASTS = 31_536_000

#: The way back from the authorization page: given its parameters, the path
#: and query the browser requests on pgn-dl's listener.
Answer = Callable[[dict[str, str]], str]


def authorized(params: dict[str, str]) -> str:
    return "/?" + urlencode({"code": CODE, "state": params["state"]})


def cancelled(params: dict[str, str]) -> str:
    query = {
        "error": "access_denied",
        "error_description": "user cancelled authorization",
        "state": params["state"],
    }
    return "/?" + urlencode(query)


def refused_scope(params: dict[str, str]) -> str:
    query = {
        "error": "invalid_scope",
        "error_description": "invalid scope: study%3Aread",
        "state": params["state"],
    }
    return "/?" + urlencode(query)


def escaping(params: dict[str, str]) -> str:
    """A refusal whose description carries a terminal escape."""
    query = {
        "error": "invalid_request",
        "error_description": "bad\x1b[2J request",
        "state": params["state"],
    }
    return "/?" + urlencode(query)


def malformed(params: dict[str, str]) -> str:
    """A request target no browser sends, which no URL parser accepts."""
    return "//["


def stale(params: dict[str, str]) -> str:
    """A tab left over from an earlier login: another sign-in's state."""
    return "/?" + urlencode({"code": "liu_SOMEONE-ELSES", "state": "not-this-one"})


def favicon(params: dict[str, str]) -> str:
    return "/favicon.ico"


class Browser:
    """The user's own browser.

    Shown the authorization page, it goes back to the ``redirect_uri`` the page
    was given, once per answer, the way a real one does after Authorize or
    Cancel. Every response it gets is kept, raw.
    """

    def __init__(self, *answers: Answer) -> None:
        self._answers = answers
        self.opened: list[str] = []
        self.params: dict[str, str] = {}
        self.responses: list[str] = []
        self._task: asyncio.Task[None] | None = None

    def open(self, url: str) -> bool:
        self.opened.append(url)
        self.params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self._task = asyncio.get_running_loop().create_task(self._go_back())
        return True

    async def _go_back(self) -> None:
        back = urlsplit(self.params["redirect_uri"])
        for answer in self._answers:
            reader, writer = await asyncio.open_connection(back.hostname, back.port)
            request = (
                f"GET {answer(self.params)} HTTP/1.1\r\nHost: {back.netloc}\r\n\r\n"
            )
            writer.write(request.encode())
            await writer.drain()
            self.responses.append((await reader.read()).decode())
            writer.close()

    async def gone_back(self) -> None:
        """Every request made and its response read."""
        if self._task is not None:
            await self._task

    def statuses(self) -> list[str]:
        return [r.split("\r\n", 1)[0] for r in self.responses]


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class Lichess:
    """The token and account endpoints, checking how each is asked."""

    def __init__(
        self,
        token: httpx.Response | Exception | None = None,
        account: httpx.Response | None = None,
    ) -> None:
        self.token = token or httpx.Response(
            200,
            json={"token_type": "Bearer", "access_token": TOKEN, "expires_in": LASTS},
        )
        self.account = account or httpx.Response(
            200, json={"id": "someone", "username": "SomeOne"}
        )
        self.exchanges: list[dict[str, str]] = []
        self.checks = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == USER_AGENT
        if (request.method, str(request.url)) == ("POST", auth.TOKEN_URL):
            assert "Authorization" not in request.headers
            form = parse_qs(request.content.decode())
            self.exchanges.append({k: v[0] for k, v in form.items()})
            if isinstance(self.token, Exception):
                raise self.token
            return self.token
        if (request.method, str(request.url)) == ("GET", ACCOUNT):
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            self.checks += 1
            return self.account
        raise AssertionError(f"unexpected {request.method} {request.url}")


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken sign-in test fails in seconds rather than in fifteen minutes."""
    monkeypatch.setattr(auth, "SIGN_IN_WAIT", 5.0)


@pytest.fixture
def signing_in(
    monkeypatch: pytest.MonkeyPatch, mock_httpx_client: Callable[..., Any]
) -> Callable[..., tuple[Browser, Lichess]]:
    def make(*answers: Answer, **lichess: Any) -> tuple[Browser, Lichess]:
        browser, site = Browser(*answers), Lichess(**lichess)
        monkeypatch.setattr(auth.webbrowser, "open", browser.open)
        monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(site))
        return browser, site

    return make


def test_a_pkce_verifier_is_long_random_and_url_safe() -> None:
    verifier, challenge = auth.pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set(string.ascii_letters + string.digits + "-._~")
    assert challenge == challenge_of(verifier)
    assert auth.pkce_pair()[0] != verifier


async def test_the_authorization_page_asks_for_the_study_scope_with_pkce(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    browser, _ = signing_in(authorized)
    await auth.login()
    [opened] = browser.opened
    page = urlsplit(opened)
    assert (page.scheme, page.netloc, page.path) == ("https", "lichess.org", "/oauth")
    params = browser.params
    assert params["response_type"] == "code"
    assert params["client_id"] == "pgn-dl"
    assert params["scope"] == "study:read"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 16
    # Back to this machine only, on a port of its own.
    back = urlsplit(params["redirect_uri"])
    assert (back.scheme, back.hostname, back.path) == ("http", "127.0.0.1", "/")
    assert back.port


async def test_authorize_then_the_code_is_exchanged_checked_and_saved(
    signing_in: Callable[..., tuple[Browser, Lichess]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser, site = signing_in(authorized)
    await auth.login()
    await browser.gone_back()

    [exchange] = site.exchanges
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == CODE
    assert exchange["client_id"] == "pgn-dl"
    assert exchange["redirect_uri"] == browser.params["redirect_uri"]
    # The verifier is the one whose hash the authorization page was given.
    assert challenge_of(exchange["code_verifier"]) == browser.params["code_challenge"]
    assert site.checks == 1
    assert auth.load_token() == TOKEN

    assert browser.statuses() == ["HTTP/1.1 200 OK"]
    assert auth.SIGNED_IN_PAGE in browser.responses[0]
    out = capsys.readouterr().out
    assert "Signed in as SomeOne" in out
    assert "lasts until" in out
    for secret in (TOKEN, CODE, exchange["code_verifier"]):
        assert secret not in out


async def test_a_cancelled_sign_in_saves_nothing_and_asks_for_no_token(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    browser, site = signing_in(cancelled)
    with pytest.raises(FetchError, match="cancelled; nothing was saved"):
        await auth.login()
    await browser.gone_back()
    assert site.exchanges == []
    assert not auth.credentials_path().exists()
    assert auth.NOT_SIGNED_IN_PAGE in browser.responses[0]


async def test_lichess_refusing_the_request_says_why(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    signing_in(refused_scope)
    with pytest.raises(FetchError) as exc:
        await auth.login()
    assert str(exc.value) == (
        "Lichess refused the sign-in: invalid scope: study%3Aread; nothing was saved"
    )
    assert not auth.credentials_path().exists()


async def test_lichess_s_reason_reaches_the_terminal_without_control_characters(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    signing_in(escaping)
    with pytest.raises(FetchError) as exc:
        await auth.login()
    assert str(exc.value) == (
        "Lichess refused the sign-in: bad[2J request; nothing was saved"
    )


async def test_a_stray_request_is_turned_away_and_the_wait_goes_on(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    browser, site = signing_in(favicon, stale, malformed, authorized)
    await auth.login()
    await browser.gone_back()
    assert browser.statuses() == [
        "HTTP/1.1 404 Not Found",
        "HTTP/1.1 400 Bad Request",
        "HTTP/1.1 400 Bad Request",
        "HTTP/1.1 200 OK",
    ]
    assert [e["code"] for e in site.exchanges] == [CODE]
    assert auth.load_token() == TOKEN


async def test_no_answer_in_time_gives_up_and_saves_nothing(
    signing_in: Callable[..., tuple[Browser, Lichess]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_in()  # the page opens, and nobody clicks anything
    monkeypatch.setattr(auth, "SIGN_IN_WAIT", 0.05)
    with pytest.raises(FetchError, match="gave up waiting") as exc:
        await auth.login()
    assert "pgn-dl login lichess" in str(exc.value)
    assert not auth.credentials_path().exists()


async def test_a_code_lichess_will_not_exchange_saves_nothing(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    refusal = httpx.Response(
        400,
        json={
            "error": "invalid_grant",
            "error_description": "hash of code_verifier does not match code_challenge",
        },
    )
    _, site = signing_in(authorized, token=refusal)
    with pytest.raises(FetchError) as exc:
        await auth.login()
    assert str(exc.value) == (
        "Lichess refused the sign-in: hash of code_verifier does not match "
        "code_challenge; nothing was saved"
    )
    assert site.checks == 0
    assert not auth.credentials_path().exists()


async def test_a_token_the_account_check_refuses_is_not_saved(
    signing_in: Callable[..., tuple[Browser, Lichess]],
) -> None:
    signing_in(authorized, account=httpx.Response(401, json={"error": "No such token"}))
    with pytest.raises(FetchError, match="refused that token; nothing was saved"):
        await auth.login()
    assert not auth.credentials_path().exists()


# Every body echoes the code, as an error page reflecting the request might.
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(500, text=f"error for {CODE}"),
            "finishing the sign-in failed: 500",
        ),
        (
            httpx.Response(400, text=f"<html>{CODE}</html>"),
            "finishing the sign-in failed: 400",
        ),
        (
            httpx.Response(200, text=f"<html>{CODE}</html>"),
            "finishing the sign-in returned a malformed response",
        ),
        (
            httpx.Response(200, json={"token_type": "Bearer", "code": CODE}),
            "finishing the sign-in returned a malformed response",
        ),
        (
            httpx.Response(200, json={"access_token": f"{CODE}\u00a0"}),
            "finishing the sign-in returned a malformed response",
        ),
        (
            httpx.ConnectTimeout(f"timed out sending {CODE}"),
            "could not reach Lichess to finish signing in: ConnectTimeout",
        ),
    ],
)
async def test_a_failed_exchange_says_why_and_never_shows_a_secret(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
    response: httpx.Response | Exception,
    message: str,
) -> None:
    monkeypatch.setattr(
        httpx, "AsyncClient", mock_httpx_client(Lichess(token=response))
    )
    verifier, _ = auth.pkce_pair()
    with pytest.raises(FetchError) as exc:
        await auth.exchange(CODE, verifier, "http://127.0.0.1:1/")
    assert str(exc.value) == message
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__


async def test_a_token_lasting_no_time_lichess_named_is_still_a_token(
    monkeypatch: pytest.MonkeyPatch, mock_httpx_client: Callable[..., Any]
) -> None:
    answer = httpx.Response(200, json={"token_type": "Bearer", "access_token": TOKEN})
    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(Lichess(token=answer)))
    verifier, _ = auth.pkce_pair()
    assert await auth.exchange(CODE, verifier, "http://127.0.0.1:1/") == (TOKEN, None)
