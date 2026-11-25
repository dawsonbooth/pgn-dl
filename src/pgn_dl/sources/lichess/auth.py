"""Lichess sign-in: OAuth with PKCE, the way Lichess asks programs to sign in.

The explorer of Lichess games answers only requests that carry a token, and a
private study only a token whose account may see it. ``login`` gets one the
way Lichess documents for apps (``lila``'s ``modules/oauth``): it opens
Lichess's authorization page in the user's own browser, where they are signed
in already, and they click Authorize. Lichess then sends the browser back to
pgn-dl, which listens on this machine's loopback address for that one request,
with a code. The code is worth a token only together with a secret that never
leaves this machine (the PKCE code verifier), so pgn-dl needs no registration
with Lichess and has no client secret to keep.

The token is checked against the account endpoint before it is saved. Lichess
issues it for a year; a refused or expired one asks for ``login`` again.

No browser automation is involved: the authorization page is opened in the
user's own browser, and nothing is typed into the terminal.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import typer

from ...core.credentials import CredentialStore
from ...core.fetch import FetchError
from .client import ROOT, USER_AGENT

#: Any name will do: Lichess registers no apps. It is shown nowhere, but the
#: token exchange must repeat it.
CLIENT_ID = "pgn-dl"
#: The one scope pgn-dl needs: private studies. The explorer needs a token of
#: any scope.
SCOPES = "study:read"
AUTHORIZE_URL = f"{ROOT}/oauth"
TOKEN_URL = f"{ROOT}/api/token"
ACCOUNT_URL = f"{ROOT}/api/account"

#: Where Lichess sends the browser back to. A literal loopback address rather
#: than ``localhost``, which may resolve to an address nothing listens on.
LOOPBACK = "127.0.0.1"
#: Seconds to wait for the user to click Authorize, as the browser sign-ins do.
SIGN_IN_WAIT = 15 * 60.0
#: Seconds a connection to the listener may take to send its request.
_REQUEST_WAIT = 10.0

_PAGE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><title>pgn-dl</title>"
    "</head><body style='font-family:system-ui,sans-serif;margin:3em'>"
    "<p>{}</p></body></html>"
)
SIGNED_IN_PAGE = _PAGE.format(
    "pgn-dl is signed in to Lichess. You can close this tab and return to the terminal."
)
NOT_SIGNED_IN_PAGE = _PAGE.format(
    "pgn-dl was not signed in to Lichess. The terminal says why."
)
STRAY_PAGE = _PAGE.format(
    "This is not the sign-in pgn-dl is waiting for. Use the window that "
    "<code>pgn-dl login lichess</code> opened."
)
NOT_FOUND_PAGE = _PAGE.format("Not found.")


#: What a token may hold. Lichess's are "lio_" and letters and digits; one
#: with anything outside printable ASCII cannot go in a header, and the error
#: httpx raises for it would quote the whole token.
_TOKEN = re.compile(r"[!-~]+")


def _require(data: object) -> dict[str, str]:
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise FetchError("lichess credentials carry no usable token")
    return {"token": token}


def _printable(text: str) -> str:
    """Lichess's own words for an error, without anything that would act on
    the terminal."""
    return "".join(c for c in text if c.isprintable())


_store = CredentialStore("lichess", validate=_require)


def credentials_path() -> Path:
    return _store.credentials_path()


def load_token() -> str:
    """The saved token, or a refusal ending in the login hint."""
    return _store.load()["token"]


def saved_token() -> str | None:
    """The saved token, or None when no one has logged in.

    For the downloads that work without one. A credentials file that exists
    but cannot be read is still refused, rather than silently ignored.
    """
    if not credentials_path().exists():
        return None
    return load_token()


def pkce_pair() -> tuple[str, str]:
    """A fresh code verifier and its S256 challenge (RFC 7636).

    The verifier is 86 characters of the URL-safe alphabet, within the 43 to
    128 the RFC allows and Lichess checks.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(redirect_uri: str, challenge: str, state: str) -> str:
    """Lichess's authorization page, asking for ``SCOPES`` on pgn-dl's behalf."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class _Redirect:
    """The request Lichess sends the browser back with, received once.

    Only a request to ``/`` carrying this sign-in's ``state`` counts. Anything
    else (a favicon, a stale tab from an earlier login, another program on this
    machine guessing) is answered and ignored, and the wait goes on.
    """

    def __init__(self, state: str) -> None:
        self._state = state.encode()
        self._answer: asyncio.Future[dict[str, str]] = (
            asyncio.get_running_loop().create_future()
        )
        self.uri = ""  # set once the listener has a port

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            async with asyncio.timeout(_REQUEST_WAIT):
                line = await reader.readline()
                while await reader.readline() not in (b"\r\n", b"\n", b""):
                    pass  # the headers: nothing in them matters here
        except TimeoutError, ValueError, ConnectionError:
            writer.close()
            return
        status, page, params = self._respond(line)
        body = page.encode()
        writer.write(
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        try:
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
        # Only now: the answer ends the wait and closes the listener, which
        # before this would cut the browser off mid-page.
        if params is not None and not self._answer.done():
            self._answer.set_result(params)

    def _respond(self, line: bytes) -> tuple[str, str, dict[str, str] | None]:
        """The status and page to answer with, and this sign-in's answer if
        the request carries it."""
        parts = line.decode("latin-1").split()
        if len(parts) < 2 or parts[0] != "GET":
            return "405 Method Not Allowed", NOT_FOUND_PAGE, None
        try:
            target = urlsplit(parts[1])
            params = {k: v[0] for k, v in parse_qs(target.query).items()}
        except ValueError:  # a target no browser sends, such as "//["
            return "400 Bad Request", NOT_FOUND_PAGE, None
        if target.path != "/":
            return "404 Not Found", NOT_FOUND_PAGE, None
        if not secrets.compare_digest(params.get("state", "").encode(), self._state):
            return "400 Bad Request", STRAY_PAGE, None
        page = SIGNED_IN_PAGE if "code" in params else NOT_SIGNED_IN_PAGE
        return "200 OK", page, params

    async def code(self, wait: float) -> str:
        """The authorization code, once the browser comes back with one."""
        try:
            async with asyncio.timeout(wait):
                params = await self._answer
        except TimeoutError:
            raise FetchError(
                "gave up waiting for lichess sign-in after "
                f"{wait / 60:.0f} minutes; nothing was saved. "
                "Run: pgn-dl login lichess to try again"
            ) from None
        if code := params.get("code"):
            return code
        error = params.get("error", "")
        if error == "access_denied":
            raise FetchError("the Lichess sign-in was cancelled; nothing was saved")
        reason = params.get("error_description") or error or "no code came back"
        raise FetchError(
            f"Lichess refused the sign-in: {_printable(reason)}; nothing was saved"
        )


@asynccontextmanager
async def _listening(state: str) -> AsyncGenerator[_Redirect]:
    """A listener on a free loopback port for the way back from Lichess."""
    redirect = _Redirect(state)
    server = await asyncio.start_server(redirect.handle, host=LOOPBACK, port=0)
    try:
        port = server.sockets[0].getsockname()[1]
        redirect.uri = f"http://{LOOPBACK}:{port}/"
        yield redirect
    finally:
        server.close()
        # A browser may hold a connection open it never sends on; waiting for
        # it to go away on its own would hold the login up.
        server.close_clients()
        await server.wait_closed()


async def exchange(
    code: str, verifier: str, redirect_uri: str
) -> tuple[str, int | None]:
    """The token ``code`` is worth, and the seconds it lasts if Lichess says.

    Neither the code, the verifier nor the token goes into a message: a
    refusal carries only Lichess's own description of what was wrong.
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": CLIENT_ID,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                TOKEN_URL, data=form, headers={"User-Agent": USER_AGENT}
            )
    except httpx.HTTPError as exc:
        raise FetchError(
            f"could not reach Lichess to finish signing in: {type(exc).__name__}"
        ) from None
    try:
        data: Any = resp.json()
    except ValueError:
        data = None
    if resp.status_code == 400 and isinstance(data, dict):
        reason = data.get("error_description") or data.get("error")
        if isinstance(reason, str) and reason:
            raise FetchError(
                f"Lichess refused the sign-in: {_printable(reason)}; nothing was saved"
            )
    if resp.status_code != 200:
        raise FetchError(f"finishing the sign-in failed: {resp.status_code}")
    if not isinstance(data, dict):
        raise FetchError("finishing the sign-in returned a malformed response")
    token = data.get("access_token")
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise FetchError("finishing the sign-in returned a malformed response")
    lasts = data.get("expires_in")
    return token, lasts if type(lasts) is int and lasts > 0 else None


async def account_name(token: str) -> str:
    """The username ``token`` belongs to, or a refusal that never shows it."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(ACCOUNT_URL, headers=headers)
    except httpx.HTTPError as exc:
        raise FetchError(
            f"could not reach Lichess to check the token: {type(exc).__name__}"
        ) from None
    if resp.status_code == 401:
        raise FetchError("Lichess refused that token; nothing was saved")
    if resp.status_code != 200:
        raise FetchError(f"checking the token failed: {resp.status_code}")
    try:
        data: Any = resp.json()
    except ValueError:
        raise FetchError("checking the token returned a malformed response") from None
    username = data.get("username") if isinstance(data, dict) else None
    if not isinstance(username, str) or not username:
        raise FetchError("checking the token returned a malformed response")
    return username


async def login() -> None:
    """Have the user authorize pgn-dl on Lichess, then check and save the token."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    async with _listening(state) as redirect:
        url = authorize_url(redirect.uri, challenge, state)
        typer.echo(
            "Click Authorize on the Lichess page that opened in your browser; "
            "this exits automatically."
        )
        typer.echo(f"If no page opened, visit:\n  {url}")
        webbrowser.open(url)
        code = await redirect.code(SIGN_IN_WAIT)
        redirect_uri = redirect.uri
    token, lasts = await exchange(code, verifier, redirect_uri)
    username = await account_name(token)
    _store.save({"token": token})
    typer.echo(f"Signed in as {username}. Token saved to {credentials_path()}")
    if lasts is not None:
        until = (datetime.now(UTC) + timedelta(seconds=lasts)).date()
        typer.echo(f"It lasts until {until}; then run pgn-dl login lichess again.")
