"""chessable sign-in and credential storage.

Sign-in is interactive and happens once, in a browser: Chessable logs in through
chess.com (and Google/Apple/email), behind a captcha, so it needs a real window.
What persists afterwards is the JWT Chessable keeps in ``localStorage`` plus the
account's user id, read out of the signed-in browser and stored owner-only. From
then on every download is plain HTTPS carrying ``Authorization: Bearer <jwt>``;
Chessable has no refresh endpoint, so an expired JWT is fixed by signing in again.

The stored JWT is a long-lived credential: it is written owner-only and must
never be logged, echoed, or included in an error message.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer

from ...core.fetch import FetchError
from ...core.io import private_dir, write_text_atomic
from ...core.paths import profile_dir as _source_profile_dir

#: The ``localStorage`` keys Chessable's web app stores the session under, on the
#: production origin. The login flow reads the JWT (the credential) and the UID
#: (the account id the API is queried with) straight out of these.
JWT_KEY = "chessable.web.production.JWT"
UID_KEY = "chessable.web.production.UID"

_LOGIN_HINT = "run: pgn-dl login chessable"


def profile_dir() -> Path:
    """Directory holding the credential file and chessable's own Chrome profile."""
    return _source_profile_dir("chessable")


def credentials_path() -> Path:
    return profile_dir() / "credentials.json"


def _require(jwt: object, uid: object) -> dict[str, str]:
    """The credential as a validated dict, or a refusal that names the missing
    field rather than the value: the value is the token, and refusals are seen."""
    if not isinstance(jwt, str) or not jwt:
        raise FetchError("chessable credentials carry no jwt")
    if not isinstance(uid, str) or not uid:
        raise FetchError("chessable credentials carry no uid")
    return {"jwt": jwt, "uid": uid}


def save_credentials(*, jwt: str, uid: str) -> None:
    """Persist the JWT and uid owner-only, replacing any previous value.

    Written whole or not at all, for the reason noctie's store is: a truncated
    credentials.json locks the user out until they sign in again.
    """
    payload = _require(jwt, uid)  # validate before any filesystem side effect
    private_dir(profile_dir())
    path = credentials_path()
    # The rename below would replace a link rather than write through it, but a
    # link here is either an attack or an arrangement we should not silently
    # destroy, so say so instead.
    if path.is_symlink():
        raise FetchError(
            f"{path} is a symbolic link; refusing to write credentials through it"
        )
    try:
        write_text_atomic(path, json.dumps(payload))
    except OSError as exc:
        # The message, never the token: this reaches the runner and the log.
        reason = exc.strerror or type(exc).__name__
        raise FetchError(
            f"could not save chessable credentials to {path}: {reason}"
        ) from None


def load_credentials() -> dict[str, str]:
    """The stored ``{"jwt", "uid"}``, or a refusal that says how to fix it."""
    path = credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise FetchError(
            f"no saved chessable credentials at {path}; {_LOGIN_HINT}"
        ) from None
    if not isinstance(data, dict):
        raise FetchError(f"chessable credentials are not a map; {_LOGIN_HINT}")
    try:
        return _require(data.get("jwt"), data.get("uid"))
    except FetchError as exc:
        raise FetchError(f"{exc}; {_LOGIN_HINT}") from None


_READ_STORAGE_JS = """(keys) => {
  const out = {};
  for (const k of keys) out[k] = localStorage.getItem(k);
  return out;
}"""


async def _read_credentials(page: Any) -> dict[str, str] | None:
    """The stored JWT+uid from the page's ``localStorage``, or None if not there.

    Returns None rather than raising when the read races a navigation: during the
    chess.com OAuth hand-off the page navigates repeatedly, and an ``evaluate``
    that lands mid-navigation raises Playwright's "Execution context was
    destroyed". That is a transient not-signed-in-yet state, polled through
    rather than fatal. A non-Playwright error is a defect here and is re-raised.
    """
    from ...core.browser import is_playwright_error

    try:
        values = await page.evaluate(_READ_STORAGE_JS, [JWT_KEY, UID_KEY])
    except Exception as exc:
        if is_playwright_error(exc):
            return None
        raise
    jwt, uid = values.get(JWT_KEY), values.get(UID_KEY)
    if jwt and uid:
        return {"jwt": jwt, "uid": uid}
    return None


_SIGN_IN_POLLS = 300  # every 3 seconds, so 15 minutes


async def login() -> None:
    """Open a browser so the user can sign in, then store the JWT and uid.

    Chessable keeps its session as a ``localStorage`` JWT rather than a cookie,
    so it is read out of the app's own storage once sign-in completes. The login
    page redirects into chess.com's OAuth (and its captcha); the real Chrome
    profile core.browser opens is what gets that through, the same way
    ``login chesscom`` does.
    """
    from ...core.browser import browser_context

    typer.echo(
        "A browser window is open. Sign in to Chessable; this exits automatically."
    )
    # Owner-only before the browser writes its own copy of the session into this
    # directory's localStorage.
    private_dir(profile_dir())
    async with browser_context(profile_dir(), headless=False) as ctx:
        page = await ctx.new_page()
        await page.goto(
            "https://www.chessable.com/login/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        for _ in range(_SIGN_IN_POLLS):
            await asyncio.sleep(3)
            creds = await _read_credentials(page)
            if creds is not None:
                save_credentials(**creds)
                typer.echo(f"Signed in. Credentials saved to {credentials_path()}")
                return
        raise FetchError(
            "gave up waiting for chessable sign-in after 15 minutes; "
            "nothing was saved. Run: pgn-dl login chessable to try again"
        )
