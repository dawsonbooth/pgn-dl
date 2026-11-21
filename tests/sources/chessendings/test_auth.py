"""chessendings's coordinates on the shared Supabase session.

The session schema, cookie codec, probe and refresh choreography are tested in
tests/test_supabase.py. This suite covers what chessendings supplies: its
name, token endpoint, anon key and sign-in page.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessendings import auth

from ...conftest import SUPABASE_SESSION, SignInBrowser, supabase_cookie


def test_the_anon_key_belongs_to_the_project() -> None:
    claims = auth.ANON_KEY.split(".")[1]
    decoded = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    assert (decoded["ref"], decoded["role"]) == (auth.PROJECT, "anon")


async def test_no_session_is_saved_until_login() -> None:
    assert auth.has_saved_session() is False
    with pytest.raises(FetchError, match="login chessendings"):
        await auth.session_cookie()


async def test_a_refresh_goes_to_the_project_with_its_anon_key(
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
    cookie = await auth.session_cookie()
    assert cookie.startswith(f"{auth.COOKIE_NAME}=base64-")
    req = seen[0]
    assert req.url.host == "ndkofotnyjuqsfiicbnp.supabase.co"
    assert req.url.path == "/auth/v1/token"
    assert req.headers["apikey"] == auth.ANON_KEY
    assert auth.session.load()["refresh_token"] == "DUMMY-ROTATED"


async def test_login_signs_in_at_chessendings_and_saves_the_cookie_session(
    sign_in_browser: SignInBrowser, capsys: pytest.CaptureFixture[str]
) -> None:
    sign_in_browser.cookies = [
        {"name": auth.COOKIE_NAME, "value": supabase_cookie(SUPABASE_SESSION)}
    ]
    await auth.login()
    assert sign_in_browser.visited == ["https://chessendings.com/login"]
    assert sign_in_browser.asked == ["https://chessendings.com"]
    assert "Sign in to ChessEndings;" in capsys.readouterr().out
    assert auth.has_saved_session() is True
    assert auth.session.load() == SUPABASE_SESSION
