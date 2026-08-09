"""noctie sign-in and credential storage.

Sign-in is interactive and happens once, in a browser. What persists afterwards
is a Firebase refresh token, exchanged for a one-hour ID token on each run, so
downloading needs no browser at all.

The stored refresh token is a long-lived credential: it is written owner-only and
must never be logged, echoed, or included in an error message.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import typer

from ...core.fetch import FetchError
from ...core.io import private_dir, write_text_atomic
from ...core.paths import profile_dir as _source_profile_dir

# Firebase web API keys are public: they ship in every client and identify the
# project rather than authorising anything on their own.
API_KEY = "AIzaSyCGHQu5-bSa_unMnNSqCdATmTYiXBvnviE"
_TOKEN_URL = f"https://securetoken.googleapis.com/v1/token?key={API_KEY}"

_LOGIN_HINT = "run: pgn-dl login noctie"


def profile_dir() -> Path:
    """Directory holding the credential file and noctie's own Chrome profile."""
    return _source_profile_dir("noctie")


def credentials_path() -> Path:
    return profile_dir() / "credentials.json"


def save_refresh_token(token: str) -> None:
    """Persist the refresh token owner-only, replacing any previous value.

    Written whole or not at all: a truncated or empty credentials.json locks
    the user out until they sign in again, and two runs rotating the token at
    once would otherwise blend their writes into one that parses and is valid
    for neither.
    """
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
        write_text_atomic(path, json.dumps({"refresh_token": token}))
    except OSError as exc:
        # The message, never the token: this reaches the runner and the log.
        reason = exc.strerror or type(exc).__name__
        raise FetchError(
            f"could not save noctie credentials to {path}: {reason}"
        ) from None


def load_refresh_token() -> str:
    path = credentials_path()
    try:
        token = json.loads(path.read_text(encoding="utf-8"))["refresh_token"]
    except OSError, KeyError, ValueError:
        raise FetchError(
            f"no saved noctie credentials at {path}; {_LOGIN_HINT}"
        ) from None
    if not token:
        raise FetchError(f"saved noctie credentials are empty; {_LOGIN_HINT}")
    return token


async def _exchange(refresh_token: str) -> dict[str, Any]:
    """Trade a refresh token for a fresh ID token. The only place it is sent.

    Every failure leaves here as a :class:`FetchError` so the runner reports it
    rather than a traceback, and none of them carries the response body: the
    securetoken endpoint echoes the request, so the body contains the token.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            )
    except httpx.HTTPError as exc:
        # The exception type only: httpx renders the request in some messages.
        raise FetchError(
            f"noctie token refresh could not reach Google: {type(exc).__name__}"
        ) from None
    if resp.status_code != 200:
        raise FetchError(f"noctie token refresh failed: {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise FetchError("noctie token refresh returned a malformed response") from None


async def id_token() -> str:
    """A fresh ID token, persisting the rotated refresh token that comes back."""
    # Outside the try: load_refresh_token's own message already ends with the
    # hint, and appending it again would print it twice.
    refresh = load_refresh_token()
    try:
        data = await _exchange(refresh)
    except FetchError as exc:
        raise FetchError(f"{exc}; {_LOGIN_HINT}") from None
    if rotated := data.get("refresh_token"):
        try:
            save_refresh_token(rotated)
        except FetchError as exc:
            # Google has already rotated it, so the one on disk is spent
            # whatever happens next: the user signs in again either way.
            raise FetchError(f"{exc}; {_LOGIN_HINT}") from None
    if not (token := data.get("id_token")):
        raise FetchError(f"noctie token refresh returned no ID token; {_LOGIN_HINT}")
    return token


# Firebase creates the store on its first write, so between leaving the login
# page and the session being persisted there is a window where the database
# exists and the store does not. Opening a transaction on a missing store
# throws, so answer "nothing yet" and let the caller poll again.
_IDB_JS = """async () => {
  const open = (n) => new Promise((res, rej) => {
    const r = indexedDB.open(n); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
  const db = await open('firebaseLocalStorageDb');
  if (!db.objectStoreNames.contains('firebaseLocalStorage')) return [];
  const store = db.transaction('firebaseLocalStorage', 'readonly').objectStore('firebaseLocalStorage');
  return await new Promise((res, rej) => {
    const r = store.getAll(); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}"""

_SIGN_IN_POLLS = 300  # every 3 seconds, so 15 minutes


async def login() -> None:
    """Open a browser so the user can sign in, then store the refresh token.

    noctie keeps its Firebase session in IndexedDB rather than cookies, so the
    token is read out of the app's own store once sign-in completes.
    """
    from ...core.browser import browser_context

    typer.echo("A browser window is open. Sign in to noctie; this exits automatically.")
    # Owner-only before the browser writes its own copy of the refresh token
    # into this directory's IndexedDB.
    private_dir(profile_dir())
    async with browser_context(profile_dir(), headless=False) as ctx:
        page = await ctx.new_page()
        await page.goto(
            "https://app.noctie.ai/login?redirectPath=%2Fthemes",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        for _ in range(_SIGN_IN_POLLS):
            await asyncio.sleep(3)
            if "/login" in page.url:
                continue
            entries = await page.evaluate(_IDB_JS)
            for entry in entries:
                manager = (entry.get("value") or {}).get("stsTokenManager") or {}
                if refresh := manager.get("refreshToken"):
                    save_refresh_token(refresh)
                    typer.echo(f"Signed in. Credentials saved to {credentials_path()}")
                    return
        raise FetchError(
            "gave up waiting for noctie sign-in after 15 minutes; "
            "nothing was saved. Run: pgn-dl login noctie to try again"
        )
