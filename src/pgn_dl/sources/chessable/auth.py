"""chessable sign-in and credential storage.

Sign-in is interactive and happens once, in a browser: Chessable logs in through
chess.com (and Google/Apple/email), behind a captcha, so it needs a real window.
What persists afterwards is the JWT Chessable keeps in ``localStorage`` plus the
account's user id, read out of the signed-in browser and stored owner-only. From
then on every download is plain HTTPS carrying ``Authorization: Bearer <jwt>``;
Chessable has no refresh endpoint, so an expired JWT is fixed by signing in again.

Storage and the sign-in loop are the credential store's (see CONTEXT.md); this
module supplies chessable's schema and its browser probe.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.credentials import CredentialStore
from ...core.fetch import FetchError

#: The ``localStorage`` keys Chessable's web app stores the session under, on the
#: production origin. The login flow reads the JWT (the credential) and the UID
#: (the account id the API is queried with) straight out of these.
JWT_KEY = "chessable.web.production.JWT"
UID_KEY = "chessable.web.production.UID"


def _require(data: object) -> dict[str, str]:
    """The credential as a validated dict, or a refusal that names the missing
    field rather than the value: the value is the token, and refusals are seen."""
    jwt = data.get("jwt") if isinstance(data, dict) else None
    uid = data.get("uid") if isinstance(data, dict) else None
    if not isinstance(jwt, str) or not jwt:
        raise FetchError("chessable credentials carry no jwt")
    if not isinstance(uid, str) or not uid:
        raise FetchError("chessable credentials carry no uid")
    return {"jwt": jwt, "uid": uid}


_store = CredentialStore("chessable", validate=_require)


def profile_dir() -> Path:
    """Directory holding the credential file and chessable's own Chrome profile."""
    return _store.profile_dir()


def credentials_path() -> Path:
    return _store.credentials_path()


def save_credentials(*, jwt: str, uid: str) -> None:
    """Persist the JWT and uid owner-only, replacing any previous value."""
    _store.save({"jwt": jwt, "uid": uid})


def load_credentials() -> dict[str, str]:
    """The stored ``{"jwt", "uid"}``, or a refusal that says how to fix it."""
    return _store.load()


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


async def _probe(ctx: Any, page: Any) -> dict[str, str] | None:
    return await _read_credentials(page)


async def login() -> None:
    """Open a browser so the user can sign in, then store the JWT and uid.

    Chessable keeps its session as a ``localStorage`` JWT rather than a cookie,
    so it is read out of the app's own storage once sign-in completes. The login
    page redirects into chess.com's OAuth (and its captcha); the real Chrome
    profile core.browser opens is what gets that through, the same way
    ``login chesscom`` does.
    """
    await _store.sign_in(
        login_url="https://www.chessable.com/login/",
        probe=_probe,
        display_name="Chessable",
    )
