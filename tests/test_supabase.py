"""The shared Supabase session: schema, cookie codec, probe, refresh choreography.

Storage invariants (permissions, symlink refusal, atomicity) and the refresh
call's error taxonomy are the credential store's and are tested once in
tests/core/test_credentials.py. An invented project stands in for a source's
own; each source's suite checks only its coordinates.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.supabase import SupabaseAuth

from .conftest import SUPABASE_SESSION, supabase_cookie

COOKIE = "sb-widgetref-auth-token"
ANON_KEY = "DUMMY-ANON-KEY"


def _auth() -> SupabaseAuth:
    return SupabaseAuth(
        "widget",
        auth_url="https://widgetref.supabase.co/auth/v1",
        anon_key=ANON_KEY,
        cookie_name=COOKIE,
        site="https://widget.example",
    )


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


def test_missing_credentials_tell_the_user_how_to_fix_it() -> None:
    with pytest.raises(FetchError) as exc:
        _auth().load()
    assert "login widget" in str(exc.value)


def test_has_saved_session_reports_whether_login_saved_anything() -> None:
    auth = _auth()
    assert auth.has_saved_session() is False
    auth.save(SUPABASE_SESSION)
    assert auth.has_saved_session() is True


def test_a_session_without_both_tokens_is_refused() -> None:
    with pytest.raises(FetchError):
        _auth().save({"access_token": "A"})
    with pytest.raises(FetchError):
        _auth().save({"refresh_token": "R"})


def test_decode_cookie_reads_the_supabase_ssr_form() -> None:
    assert _auth().decode_cookie(supabase_cookie(SUPABASE_SESSION)) == SUPABASE_SESSION


def test_decode_cookie_reads_padded_and_percent_encoded_values() -> None:
    raw = json.dumps(SUPABASE_SESSION).encode()
    padded = "base64-" + base64.urlsafe_b64encode(raw).decode()  # keeps '='
    assert _auth().decode_cookie(padded) == SUPABASE_SESSION
    assert _auth().decode_cookie(padded.replace("=", "%3D")) == SUPABASE_SESSION


def test_decode_cookie_reads_legacy_plain_json() -> None:
    assert _auth().decode_cookie(json.dumps(SUPABASE_SESSION)) == SUPABASE_SESSION


def test_decode_cookie_keeps_a_literal_percent_escape_in_plain_json() -> None:
    # Unquoting unconditionally would turn "%3D" into "=" inside the session.
    session = SUPABASE_SESSION | {"note": "path%3Dvalue"}
    assert _auth().decode_cookie(json.dumps(session))["note"] == "path%3Dvalue"


def test_decode_cookie_refuses_garbage_without_echoing_it() -> None:
    auth = _auth()
    with pytest.raises(FetchError) as exc:
        auth.decode_cookie("base64-!!!not-base64!!!")
    assert "!!!" not in str(exc.value)
    assert COOKIE in str(exc.value)
    with pytest.raises(FetchError):
        auth.decode_cookie("just some words")
    with pytest.raises(FetchError):
        auth.decode_cookie(json.dumps(["legacy", "array", "form"]))


def test_encode_cookie_round_trips_and_is_unpadded() -> None:
    value = SupabaseAuth.encode_cookie(SUPABASE_SESSION)
    assert value.startswith("base64-")
    assert "=" not in value
    assert _auth().decode_cookie(value) == SUPABASE_SESSION


def test_join_chunks_prefers_the_whole_cookie() -> None:
    cookies = [
        {"name": COOKIE, "value": "whole"},
        {"name": f"{COOKIE}.0", "value": "chunk0"},
    ]
    assert _auth().join_chunks(cookies) == "whole"


def test_join_chunks_reassembles_numeric_suffixes_in_order() -> None:
    cookies = [
        {"name": f"{COOKIE}.10", "value": "C"},
        {"name": f"{COOKIE}.0", "value": "A"},
        {"name": f"{COOKIE}.2", "value": "B"},
        {"name": "unrelated", "value": "x"},
    ]
    assert _auth().join_chunks(cookies) == "ABC"


def test_join_chunks_answers_none_when_absent() -> None:
    assert _auth().join_chunks([{"name": "other", "value": "x"}]) is None


def _fresh(expires_in: int) -> dict:
    return SUPABASE_SESSION | {"expires_at": int(time.time()) + expires_in}


async def test_a_fresh_session_is_used_without_refreshing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()
    auth.save(_fresh(3600))

    async def explode(refresh_token: str) -> dict:
        raise AssertionError("a fresh session must not be refreshed")

    monkeypatch.setattr(auth, "refresh", explode)
    value = await auth.cookie()
    assert value.startswith(f"{COOKIE}=base64-")
    assert auth.decode_cookie(value.split("=", 1)[1]) == auth.load()


async def test_an_expiring_session_is_refreshed_and_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()
    auth.save(_fresh(60))  # inside the 5-minute margin
    calls: list[str] = []

    async def fake_refresh(refresh_token: str) -> dict:
        calls.append(refresh_token)
        return {
            "access_token": "DUMMY-ACCESS-2",
            "refresh_token": "DUMMY-ROTATED",
            "expires_in": 3600,
            "token_type": "bearer",
            "user": {"id": "u-1"},
        }

    monkeypatch.setattr(auth, "refresh", fake_refresh)
    value = await auth.cookie()
    assert calls == ["DUMMY-REFRESH-DO-NOT-LEAK"]
    stored = auth.load()
    # The rotated refresh token is what a later run must use.
    assert stored["refresh_token"] == "DUMMY-ROTATED"
    # A response without expires_at gets one computed from expires_in.
    assert stored["expires_at"] > time.time() + 3000
    assert auth.decode_cookie(value.split("=", 1)[1]) == stored


async def test_a_session_without_expiry_is_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()
    auth.save(
        {"access_token": "DUMMY-A", "refresh_token": "DUMMY-R", "user": {"id": "u-1"}}
    )

    async def fake_refresh(refresh_token: str) -> dict:
        return {
            "access_token": "DUMMY-A2",
            "refresh_token": "DUMMY-R2",
            "expires_at": int(time.time()) + 3600,
        }

    monkeypatch.setattr(auth, "refresh", fake_refresh)
    await auth.cookie()
    stored = auth.load()
    assert stored["refresh_token"] == "DUMMY-R2"
    # A response that omits a field the session carried must not strip it
    # from the cookie the server parses next.
    assert stored["user"] == {"id": "u-1"}


async def test_a_failure_to_persist_the_rotation_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # By this point Supabase has rotated the token, so the one on disk is
    # spent; the message must say sign-in is the fix, and must not carry the
    # rotated token.
    from pgn_dl.core import io as io_module

    auth = _auth()
    auth.save(_fresh(60))

    async def fake_refresh(refresh_token: str) -> dict:
        return {"access_token": "DUMMY-A2", "refresh_token": "DUMMY-ROTATED-2"}

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(auth, "refresh", fake_refresh)
    monkeypatch.setattr(io_module.os, "replace", no_space)
    with pytest.raises(FetchError) as exc:
        await auth.cookie()
    assert "login widget" in str(exc.value)
    assert "DUMMY-ROTATED-2" not in str(exc.value)


async def test_a_malformed_refresh_response_ends_in_the_login_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth()
    auth.save(_fresh(60))

    async def fake_refresh(refresh_token: str) -> dict:
        raise FetchError("widget token refresh returned a malformed response")

    monkeypatch.setattr(auth, "refresh", fake_refresh)
    with pytest.raises(FetchError) as exc:
        await auth.cookie()
    assert "login widget" in str(exc.value)


async def test_a_real_refresh_posts_the_anon_key_and_persists_rotation(
    monkeypatch: pytest.MonkeyPatch, mock_httpx_client: Callable[..., Any]
) -> None:
    auth = _auth()
    auth.save(_fresh(60))
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
    await auth.cookie()
    req = seen[0]
    assert req.url.host == "widgetref.supabase.co"
    assert req.url.path == "/auth/v1/token"
    assert req.url.params["grant_type"] == "refresh_token"
    assert req.headers["apikey"] == ANON_KEY
    assert json.loads(req.content) == {"refresh_token": "DUMMY-REFRESH-DO-NOT-LEAK"}
    assert auth.load()["refresh_token"] == "DUMMY-ROTATED"


async def test_a_non_object_refresh_response_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The response is merged as a mapping over the stored session, which a
    # list or scalar cannot be.
    auth = _auth()

    async def fake_store_refresh(*args: object, **kwargs: object) -> list:
        return ["not", "a", "map"]

    monkeypatch.setattr(auth._store, "refresh", fake_store_refresh)
    with pytest.raises(FetchError, match="non-object response"):
        await auth.refresh("DUMMY-REFRESH-DO-NOT-LEAK")


class FakeCtx:
    def __init__(self, cookies: list[dict]) -> None:
        self._cookies = cookies
        self.asked: list[str] = []

    async def cookies(self, url: str) -> list[dict]:
        self.asked.append(url)
        return self._cookies


async def test_probe_returns_none_until_the_cookie_is_a_readable_session() -> None:
    assert await _auth().probe(FakeCtx([]), None) is None
    ctx = FakeCtx([{"name": COOKIE, "value": "not-a-session"}])
    assert await _auth().probe(ctx, None) is None


async def test_probe_returns_the_decoded_session_once_the_cookie_appears() -> None:
    ctx = FakeCtx([{"name": COOKIE, "value": supabase_cookie(SUPABASE_SESSION)}])
    assert await _auth().probe(ctx, None) == SUPABASE_SESSION
    assert ctx.asked == ["https://widget.example"]


async def test_probe_keeps_waiting_through_an_anonymous_session() -> None:
    # A site that hands guests an anonymous session would otherwise be read as
    # signed in before the user has done anything.
    guest = SUPABASE_SESSION | {"user": {"id": "u-2", "is_anonymous": True}}
    ctx = FakeCtx([{"name": COOKIE, "value": supabase_cookie(guest)}])
    assert await _auth().probe(ctx, None) is None
    member = SUPABASE_SESSION | {"user": {"id": "u-3", "is_anonymous": False}}
    ctx = FakeCtx([{"name": COOKIE, "value": supabase_cookie(member)}])
    assert await _auth().probe(ctx, None) == member
