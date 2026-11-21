"""chessreps's coordinates on the shared Supabase session.

The session schema, cookie codec, probe and refresh choreography are tested in
tests/test_supabase.py, and storage in tests/core/test_credentials.py. This
suite covers what chessreps supplies: its name, cookie, token endpoint, anon
key and sign-in page.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessreps import auth

from ...conftest import SUPABASE_SESSION, SignInBrowser, supabase_cookie


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


def test_missing_credentials_tell_the_user_how_to_fix_it() -> None:
    with pytest.raises(FetchError, match="login chessreps"):
        auth.session.load()


async def test_a_fresh_session_goes_out_as_the_chessreps_cookie() -> None:
    auth.session.save(SUPABASE_SESSION)
    name, _, value = (await auth.session_cookie()).partition("=")
    assert name == "sb-api-auth-token"
    assert auth.session.decode_cookie(value) == SUPABASE_SESSION


async def test_a_refresh_goes_to_chessreps_with_its_anon_key(
    monkeypatch: pytest.MonkeyPatch, mock_httpx_client: Callable[..., Any]
) -> None:
    auth.session.save(SUPABASE_SESSION | {"expires_at": int(time.time()) + 60})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "DUMMY-ACCESS-2",
                "refresh_token": "DUMMY-ROTATED",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    await auth.session_cookie()
    req = seen[0]
    assert req.url.host == "api.chessreps.com"
    assert req.url.path == "/auth/v1/token"
    assert req.headers["apikey"] == auth.ANON_KEY
    assert auth.session.load()["refresh_token"] == "DUMMY-ROTATED"


async def test_login_signs_in_at_chessreps_and_saves_the_cookie_session(
    sign_in_browser: SignInBrowser,
) -> None:
    sign_in_browser.cookies = [
        {"name": auth.COOKIE_NAME, "value": supabase_cookie(SUPABASE_SESSION)}
    ]
    await auth.login()
    assert sign_in_browser.visited == ["https://www.chessreps.com/signin"]
    assert sign_in_browser.asked == ["https://www.chessreps.com"]
    assert auth.session.load() == SUPABASE_SESSION
