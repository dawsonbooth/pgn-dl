"""chessreps sign-in and credential storage.

Sign-in is interactive and happens once, in a browser. What persists afterwards
is the Supabase session out of the ``sb-api-auth-token`` cookie - access token,
refresh token and all - refreshed against Supabase's token endpoint on later
runs, so downloading needs no browser at all.

The stored session is a long-lived credential: it is written owner-only and
must never be logged, echoed, or included in an error message.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import typer

from ...core.fetch import FetchError
from ...core.io import private_dir, write_text_atomic
from ...core.paths import profile_dir as _source_profile_dir

#: The one cookie chessreps's SvelteKit server reads the session from,
#: in @supabase/ssr form. Past ~3180 bytes the browser stores it chunked as
#: ``<name>.0``, ``<name>.1``, ...; the server accepts the unchunked name.
COOKIE_NAME = "sb-api-auth-token"

# Supabase anon keys are public: they ship in every page of the site and
# identify the project rather than authorising anything on their own.
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpreG1yeGVlZXFscGV0b3RkY2l3Iiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3MzQ4MTY1NTYsImV4cCI6MjA1MDM5MjU1Nn0."
    "K1-eR7j9WhVOOEKhOo91LzyW2XMBF8XeVX90BT7zfHs"
)
_TOKEN_URL = "https://api.chessreps.com/auth/v1/token?grant_type=refresh_token"

#: Refresh when the access token has less life left than this. A run makes one
#: data request, so this margin is the whole expiry story: there is no mid-run
#: re-mint to fall back on, and none needed.
_EXPIRY_MARGIN = 300.0

_LOGIN_HINT = "run: pgn-dl login chessreps"


def profile_dir() -> Path:
    """Directory holding the credential file and chessreps's Chrome profile."""
    return _source_profile_dir("chessreps")


def credentials_path() -> Path:
    return profile_dir() / "credentials.json"


def _require_tokens(session: object) -> dict:
    """The session as a dict, or a refusal that names the shape, not the value."""
    if not isinstance(session, dict):
        raise FetchError(f"chessreps session is a {type(session).__name__}, not a map")
    for field in ("access_token", "refresh_token"):
        if not isinstance(session.get(field), str) or not session[field]:
            raise FetchError(f"chessreps session carries no {field}")
    return session


def save_session(session: dict) -> None:
    """Persist the session owner-only, replacing any previous value.

    Written whole or not at all, for the reason noctie's store is: a truncated
    credentials.json locks the user out until they sign in again.
    """
    _require_tokens(session)
    private_dir(profile_dir())
    path = credentials_path()
    # The rename below would replace a link rather than write through it, but
    # a link here is either an attack or an arrangement we should not silently
    # destroy, so say so instead.
    if path.is_symlink():
        raise FetchError(
            f"{path} is a symbolic link; refusing to write credentials through it"
        )
    try:
        write_text_atomic(path, json.dumps(session))
    except OSError as exc:
        # The message, never the tokens: this reaches the runner and the log.
        reason = exc.strerror or type(exc).__name__
        raise FetchError(
            f"could not save chessreps credentials to {path}: {reason}"
        ) from None


def load_session() -> dict:
    path = credentials_path()
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise FetchError(
            f"no saved chessreps credentials at {path}; {_LOGIN_HINT}"
        ) from None
    try:
        return _require_tokens(session)
    except FetchError as exc:
        raise FetchError(f"{exc}; {_LOGIN_HINT}") from None


def decode_cookie(value: str) -> dict:
    """The session inside one ``sb-api-auth-token`` value.

    @supabase/ssr writes ``base64-`` + unpadded base64url JSON; older clients
    wrote the JSON plain. The value is tried as sent first and percent-decoded
    second: some cookie stores hand it back URL-encoded, but a plain-JSON
    session could itself contain a literal percent escape that unquoting would
    mangle. Anything else is refused by shape - the value is a credential and
    must not appear in the error.
    """
    for text in (value, unquote(value)):
        if text.startswith("base64-"):
            b64 = text.removeprefix("base64-")
            try:
                text = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()
            except binascii.Error, UnicodeDecodeError:
                continue
        try:
            session = json.loads(text)
        except ValueError:
            continue
        if isinstance(session, dict):
            return _require_tokens(session)
    raise FetchError(f"chessreps {COOKIE_NAME} cookie is not a readable session")


def encode_cookie(session: dict) -> str:
    """One session as the cookie value the server parses, always unchunked."""
    raw = json.dumps(session, separators=(",", ":")).encode()
    return "base64-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def join_chunks(cookies: list[dict]) -> str | None:
    """The session cookie's value from a browser cookie listing, or None.

    The whole cookie wins when both forms are present, matching the reader on
    the server. Chunk order is numeric: ``.10`` sorts after ``.2``.
    """
    # A browser can hold host- and domain-scoped copies under one name; last
    # wins here, and a stale-but-valid pick self-heals at the first refresh.
    values = {c.get("name"): str(c.get("value") or "") for c in cookies}
    if whole := values.get(COOKIE_NAME):
        return whole
    prefix = f"{COOKIE_NAME}."
    chunks = [
        (int(name.removeprefix(prefix)), value)
        for name, value in values.items()
        if name and name.startswith(prefix) and name.removeprefix(prefix).isdigit()
    ]
    if not chunks:
        return None
    return "".join(value for _, value in sorted(chunks))


async def _refresh(refresh_token: str) -> dict:
    """Trade a refresh token for a fresh session.

    Every failure leaves here as a :class:`FetchError` so the runner reports it
    rather than a traceback, and none of them carries the response body: token
    endpoints echo the request, so the body may contain the token.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={"refresh_token": refresh_token},
            )
    except httpx.HTTPError as exc:
        # The exception type only: httpx renders the request in some messages.
        raise FetchError(
            f"chessreps token refresh could not reach the server: {type(exc).__name__}"
        ) from None
    if resp.status_code != 200:
        raise FetchError(f"chessreps token refresh failed: {resp.status_code}")
    try:
        data: Any = resp.json()
    except ValueError:
        raise FetchError(
            "chessreps token refresh returned a malformed response"
        ) from None
    if not isinstance(data, dict):
        raise FetchError("chessreps token refresh returned a non-object response")
    return data


async def session_cookie() -> str:
    """The run's ``Cookie`` header value, refreshing the session if it is stale.

    Supabase rotates the refresh token on each exchange, so the refreshed
    session is persisted before it is used; losing the rotation would lock the
    user out until they sign in again.
    """
    session = load_session()
    expires_at = session.get("expires_at")
    if (
        not isinstance(expires_at, int | float)
        or expires_at - time.time() < _EXPIRY_MARGIN
    ):
        try:
            refreshed = _require_tokens(await _refresh(session["refresh_token"]))
        except FetchError as exc:
            raise FetchError(f"{exc}; {_LOGIN_HINT}") from None
        if "expires_at" not in refreshed:
            # GoTrue always sends expires_in; expires_at only sometimes.
            refreshed = refreshed | {
                "expires_at": int(time.time())
                + int(refreshed.get("expires_in") or 3600)
            }
        # Merged over the stored session, not substituted for it: a response
        # that omits a field the cookie carried (the user object, say) must
        # not strip it from the cookie the server parses next.
        session = session | refreshed
        try:
            save_session(session)
        except FetchError as exc:
            # Supabase has already rotated the refresh token, so the one on
            # disk is spent whatever happens next: the user signs in again
            # either way.
            raise FetchError(f"{exc}; {_LOGIN_HINT}") from None
    return f"{COOKIE_NAME}={encode_cookie(session)}"


_SIGN_IN_POLLS = 300  # every 3 seconds, so 15 minutes


async def login() -> None:
    """Open a browser so the user can sign in, then store the session.

    chessreps keeps its Supabase session in the ``sb-api-auth-token`` cookie
    (chunked past ~3180 bytes), so it is read back out of the browser context
    once sign-in completes. Polling rather than waiting for a navigation: the
    site signs in on the same page, and the cookie is the one signal that is
    there whichever path the user took.
    """
    from ...core.browser import browser_context

    typer.echo(
        "A browser window is open. Sign in to chessreps; this exits automatically."
    )
    # Owner-only before the browser writes its own copy of the session into
    # this directory's cookie store.
    private_dir(profile_dir())
    async with browser_context(profile_dir(), headless=False) as ctx:
        page = await ctx.new_page()
        await page.goto(
            "https://www.chessreps.com/signin",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        for _ in range(_SIGN_IN_POLLS):
            await asyncio.sleep(3)
            value = join_chunks(await ctx.cookies("https://www.chessreps.com"))
            if not value:
                continue
            try:
                session = decode_cookie(value)
            except FetchError:
                # A cookie that is not yet a session (or never will be) is not
                # worth failing the poll over; sign-in may still be in flight.
                continue
            save_session(session)
            typer.echo(f"Signed in. Credentials saved to {credentials_path()}")
            return
        raise FetchError(
            "gave up waiting for chessreps sign-in after 15 minutes; "
            "nothing was saved. Run: pgn-dl login chessreps to try again"
        )
