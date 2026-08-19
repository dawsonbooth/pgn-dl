"""chessreps sign-in and credential storage.

Sign-in is interactive and happens once, in a browser. What persists afterwards
is the Supabase session out of the ``sb-api-auth-token`` cookie - access token,
refresh token and all - refreshed against Supabase's token endpoint on later
runs, so downloading needs no browser at all.

Storage, the sign-in loop, and the refresh call's error taxonomy are the
credential store's (see CONTEXT.md); this module supplies chessreps's schema,
its cookie codec, its browser probe, and its post-refresh choreography
(merging the refreshed session over the stored one, and the expiry margin).
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ...core.credentials import CredentialStore
from ...core.fetch import FetchError

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


def _require_tokens(session: object) -> dict[str, Any]:
    """The session as a dict, or a refusal that names the shape, not the value."""
    if not isinstance(session, dict):
        raise FetchError(f"chessreps session is a {type(session).__name__}, not a map")
    for field in ("access_token", "refresh_token"):
        if not isinstance(session.get(field), str) or not session[field]:
            raise FetchError(f"chessreps session carries no {field}")
    return session


_store = CredentialStore("chessreps", validate=_require_tokens)


def profile_dir() -> Path:
    """Directory holding the credential file and chessreps's Chrome profile."""
    return _store.profile_dir()


def credentials_path() -> Path:
    return _store.credentials_path()


def save_session(session: dict) -> None:
    """Persist the session owner-only, replacing any previous value."""
    _store.save(session)


def load_session() -> dict:
    return _store.load()


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
    """Trade a refresh token for a fresh session."""
    data = await _store.refresh(
        _TOKEN_URL,
        headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
        json={"refresh_token": refresh_token},
    )
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
            raise FetchError(f"{exc}; {_store.login_hint}") from None
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
            raise FetchError(f"{exc}; {_store.login_hint}") from None
    return f"{COOKIE_NAME}={encode_cookie(session)}"


async def _probe(ctx: Any, page: Any) -> dict[str, Any] | None:
    """The session decoded from the browser's cookies, or None so far."""
    value = join_chunks(await ctx.cookies("https://www.chessreps.com"))
    if not value:
        return None
    try:
        return decode_cookie(value)
    except FetchError:
        # A cookie that is not yet a session (or never will be) is not worth
        # failing the poll over; sign-in may still be in flight.
        return None


async def login() -> None:
    """Open a browser so the user can sign in, then store the session.

    chessreps keeps its Supabase session in the ``sb-api-auth-token`` cookie
    (chunked past ~3180 bytes), so it is read back out of the browser context
    once sign-in completes. Polling rather than waiting for a navigation: the
    site signs in on the same page, and the cookie is the one signal that is
    there whichever path the user took.
    """
    await _store.sign_in(login_url="https://www.chessreps.com/signin", probe=_probe)
