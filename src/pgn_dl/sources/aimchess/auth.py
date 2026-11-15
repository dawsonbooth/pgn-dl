"""aimchess sign-in and credential storage.

Sign-in happens once, in a browser. The web app keeps a JWT in localStorage;
login reads it out and stores it, and downloads send it over plain HTTPS. The
token lasts 30 days and cannot be refreshed, so an expired one means signing in
again.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...core.credentials import CredentialStore
from ...core.fetch import FetchError

#: The localStorage key holding the JWT. The app JSON-encodes every value.
TOKEN_KEY = "token"

LOGIN_URL = "https://aimchess.com/auth/login"


def _require(data: object) -> dict[str, str]:
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise FetchError("aimchess credentials carry no token")
    return {"token": token}


_store = CredentialStore("aimchess", validate=_require)


def credentials_path() -> Path:
    return _store.credentials_path()


def save_token(token: str) -> None:
    _store.save({"token": token})


def load_token() -> str:
    return _store.load()["token"]


def is_expired(token: str, *, now: float) -> bool:
    """Whether the JWT's ``exp`` claim has passed; False if it can't be read.

    Signing in again reopens the same browser profile, which keeps the old
    token until the app's first request is refused. Without this check, login
    could save the expired token.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        claims = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
    except ValueError:  # bad base64 or JSON
        return False
    exp = claims.get("exp") if isinstance(claims, dict) else None
    return isinstance(exp, int | float) and exp <= now


async def _probe(ctx: Any, page: Any) -> dict[str, str] | None:
    """The signed-in token, or None while there isn't a current one."""
    from ...core.browser import is_playwright_error

    # "token" is a common key, so don't read it on a sign-in provider's page.
    if urlsplit(str(page.url)).hostname != "aimchess.com":
        return None
    try:
        raw = await page.evaluate("(key) => localStorage.getItem(key)", TOKEN_KEY)
    except Exception as exc:
        # An evaluate that lands mid-navigation fails with "Execution context
        # was destroyed"; that only means sign-in hasn't finished.
        if is_playwright_error(exc):
            return None
        raise
    try:
        token = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(token, str) or not token or is_expired(token, now=time.time()):
        return None
    return {"token": token}


async def login() -> None:
    """Open a browser for the user to sign in, then store the JWT."""
    await _store.sign_in(login_url=LOGIN_URL, probe=_probe, display_name="Aimchess")
