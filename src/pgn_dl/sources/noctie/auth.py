"""noctie sign-in and credential storage.

Sign-in is interactive and happens once, in a browser. What persists afterwards
is a Firebase refresh token, exchanged for a one-hour ID token on each run, so
downloading needs no browser at all.

Storage, the sign-in loop, and the refresh call's error taxonomy are the
credential store's (see CONTEXT.md); this module supplies noctie's schema, its
browser probe, and its post-refresh choreography (rotated-token persistence).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.credentials import CredentialStore
from ...core.fetch import FetchError

# Firebase web API keys are public: they ship in every client and identify the
# project rather than authorising anything on their own.
API_KEY = "AIzaSyCGHQu5-bSa_unMnNSqCdATmTYiXBvnviE"
_TOKEN_URL = f"https://securetoken.googleapis.com/v1/token?key={API_KEY}"


def _validate(data: object) -> dict[str, Any]:
    token = data.get("refresh_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise FetchError("noctie credentials carry no refresh_token")
    return {"refresh_token": token}


_store = CredentialStore("noctie", validate=_validate)


def profile_dir() -> Path:
    """Directory holding the credential file and noctie's own Chrome profile."""
    return _store.profile_dir()


def credentials_path() -> Path:
    return _store.credentials_path()


def save_refresh_token(token: str) -> None:
    """Persist the refresh token owner-only, replacing any previous value."""
    _store.save({"refresh_token": token})


def load_refresh_token() -> str:
    return _store.load()["refresh_token"]


async def _exchange(refresh_token: str) -> dict[str, Any]:
    """Trade a refresh token for a fresh ID token. The only place it is sent."""
    return await _store.refresh(
        _TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )


async def id_token() -> str:
    """A fresh ID token, persisting the rotated refresh token that comes back."""
    # Outside the try: load_refresh_token's own message already ends with the
    # hint, and appending it again would print it twice.
    refresh = load_refresh_token()
    try:
        data = await _exchange(refresh)
    except FetchError as exc:
        raise FetchError(f"{exc}; {_store.login_hint}") from None
    if rotated := data.get("refresh_token"):
        try:
            save_refresh_token(rotated)
        except FetchError as exc:
            # Google has already rotated it, so the one on disk is spent
            # whatever happens next: the user signs in again either way.
            raise FetchError(f"{exc}; {_store.login_hint}") from None
    if not (token := data.get("id_token")):
        raise FetchError(
            f"noctie token refresh returned no ID token; {_store.login_hint}"
        )
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


async def _probe(ctx: Any, page: Any) -> dict[str, Any] | None:
    """The refresh token out of the signed-in page's IndexedDB, or None so far."""
    if "/login" in page.url:
        return None
    entries = await page.evaluate(_IDB_JS)
    for entry in entries:
        manager = (entry.get("value") or {}).get("stsTokenManager") or {}
        if refresh := manager.get("refreshToken"):
            return {"refresh_token": refresh}
    return None


async def login() -> None:
    """Open a browser so the user can sign in, then store the refresh token.

    noctie keeps its Firebase session in IndexedDB rather than cookies, so the
    token is read out of the app's own store once sign-in completes.
    """
    await _store.sign_in(
        login_url="https://app.noctie.ai/login?redirectPath=%2Fthemes",
        probe=_probe,
    )
