"""Credential storage and session-cookie codec. No network, no browser."""

from __future__ import annotations

import base64
import json
import stat
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessreps import auth

# Invented throughout: nothing here is a credential, and none of it is ever
# meant to reach a message.
DUMMY_SESSION = {
    "access_token": "DUMMY-ACCESS-DO-NOT-LEAK",
    "refresh_token": "DUMMY-REFRESH-DO-NOT-LEAK",
    "expires_at": 4102444800,  # 2100-01-01, far future
    "token_type": "bearer",
    "user": {"id": "u-1"},
}


def _cookie_value(session: dict) -> str:
    raw = json.dumps(session).encode()
    return "base64-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_missing_credentials_tell_the_user_how_to_fix_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    with pytest.raises(FetchError) as exc:
        auth.load_session()
    assert "login chessreps" in str(exc.value)


def test_saved_credentials_round_trip_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(DUMMY_SESSION)
    assert auth.load_session() == DUMMY_SESSION
    mode = stat.S_IMODE(auth.credentials_path().stat().st_mode)
    assert mode == 0o600, f"credentials must be owner-only, got {mode:o}"


def test_a_session_without_both_tokens_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    with pytest.raises(FetchError):
        auth.save_session({"access_token": "A"})
    with pytest.raises(FetchError):
        auth.save_session({"refresh_token": "R"})


def test_a_symlinked_credentials_file_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path / "profile"))
    victim = tmp_path / "victim"
    victim.write_text("not a credential", encoding="utf-8")
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(victim)
    with pytest.raises(FetchError):
        auth.save_session(DUMMY_SESSION)
    assert victim.read_text(encoding="utf-8") == "not a credential"


def test_decode_cookie_reads_the_supabase_ssr_form() -> None:
    assert auth.decode_cookie(_cookie_value(DUMMY_SESSION)) == DUMMY_SESSION


def test_decode_cookie_reads_padded_and_percent_encoded_values() -> None:
    raw = json.dumps(DUMMY_SESSION).encode()
    padded = "base64-" + base64.urlsafe_b64encode(raw).decode()  # keeps '='
    assert auth.decode_cookie(padded) == DUMMY_SESSION
    assert auth.decode_cookie(padded.replace("=", "%3D")) == DUMMY_SESSION


def test_decode_cookie_reads_legacy_plain_json() -> None:
    assert auth.decode_cookie(json.dumps(DUMMY_SESSION)) == DUMMY_SESSION


def test_decode_cookie_keeps_a_literal_percent_escape_in_plain_json() -> None:
    # Unquoting unconditionally would turn "%3D" into "=" inside the session.
    session = DUMMY_SESSION | {"note": "path%3Dvalue"}
    assert auth.decode_cookie(json.dumps(session))["note"] == "path%3Dvalue"


def test_decode_cookie_refuses_garbage_without_echoing_it() -> None:
    with pytest.raises(FetchError) as exc:
        auth.decode_cookie("base64-!!!not-base64!!!")
    assert "!!!" not in str(exc.value)
    with pytest.raises(FetchError):
        auth.decode_cookie("just some words")
    with pytest.raises(FetchError):
        auth.decode_cookie(json.dumps(["legacy", "array", "form"]))


def test_encode_cookie_round_trips_and_is_unpadded() -> None:
    value = auth.encode_cookie(DUMMY_SESSION)
    assert value.startswith("base64-")
    assert "=" not in value
    assert auth.decode_cookie(value) == DUMMY_SESSION


def test_join_chunks_prefers_the_whole_cookie() -> None:
    cookies = [
        {"name": auth.COOKIE_NAME, "value": "whole"},
        {"name": f"{auth.COOKIE_NAME}.0", "value": "chunk0"},
    ]
    assert auth.join_chunks(cookies) == "whole"


def test_join_chunks_reassembles_numeric_suffixes_in_order() -> None:
    cookies = [
        {"name": f"{auth.COOKIE_NAME}.10", "value": "C"},
        {"name": f"{auth.COOKIE_NAME}.0", "value": "A"},
        {"name": f"{auth.COOKIE_NAME}.2", "value": "B"},
        {"name": "unrelated", "value": "x"},
    ]
    assert auth.join_chunks(cookies) == "ABC"


def test_join_chunks_answers_none_when_absent() -> None:
    assert auth.join_chunks([{"name": "other", "value": "x"}]) is None


def _fresh(expires_in: int) -> dict:
    return DUMMY_SESSION | {"expires_at": int(time.time()) + expires_in}


async def test_a_fresh_session_is_used_without_refreshing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(3600))

    async def explode(refresh_token: str) -> dict:
        raise AssertionError("a fresh session must not be refreshed")

    monkeypatch.setattr(auth, "_refresh", explode)
    value = await auth.session_cookie()
    assert value.startswith(f"{auth.COOKIE_NAME}=base64-")
    assert auth.decode_cookie(value.split("=", 1)[1]) == auth.load_session()


async def test_an_expiring_session_is_refreshed_and_persisted(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(60))  # inside the 5-minute margin
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

    monkeypatch.setattr(auth, "_refresh", fake_refresh)
    value = await auth.session_cookie()
    assert calls == ["DUMMY-REFRESH-DO-NOT-LEAK"]
    stored = auth.load_session()
    # The rotated refresh token is what a later run must use.
    assert stored["refresh_token"] == "DUMMY-ROTATED"
    # A response without expires_at gets one computed from expires_in.
    assert stored["expires_at"] > time.time() + 3000
    assert auth.decode_cookie(value.split("=", 1)[1]) == stored


async def test_a_session_without_expiry_is_refreshed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(
        {"access_token": "DUMMY-A", "refresh_token": "DUMMY-R", "user": {"id": "u-1"}}
    )

    async def fake_refresh(refresh_token: str) -> dict:
        return {
            "access_token": "DUMMY-A2",
            "refresh_token": "DUMMY-R2",
            "expires_at": int(time.time()) + 3600,
        }

    monkeypatch.setattr(auth, "_refresh", fake_refresh)
    await auth.session_cookie()
    stored = auth.load_session()
    assert stored["refresh_token"] == "DUMMY-R2"
    # A response that omits a field the session carried must not strip it
    # from the cookie the server parses next.
    assert stored["user"] == {"id": "u-1"}


async def test_a_failure_to_persist_the_rotation_is_actionable(
    tmp_path, monkeypatch
) -> None:
    # By this point Supabase has rotated the token, so the one on disk is
    # spent; the message must say sign-in is the fix, and must not carry the
    # rotated token.
    from pgn_dl.core import io

    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(60))

    async def fake_refresh(refresh_token: str) -> dict:
        return {"access_token": "DUMMY-A2", "refresh_token": "DUMMY-ROTATED-2"}

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(auth, "_refresh", fake_refresh)
    monkeypatch.setattr(io.os, "replace", no_space)
    with pytest.raises(FetchError) as exc:
        await auth.session_cookie()
    assert "login chessreps" in str(exc.value)
    assert "DUMMY-ROTATED-2" not in str(exc.value)


Handler = Callable[[httpx.Request], httpx.Response]


def _mock_client(handler: Handler) -> Callable[..., httpx.AsyncClient]:
    """An ``httpx.AsyncClient`` factory answering from ``handler``, opening no socket."""
    real = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    return factory


async def test_a_real_refresh_posts_the_anon_key_and_persists_rotation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(60))
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

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    await auth.session_cookie()
    req = seen[0]
    assert req.url.host == "api.chessreps.com"
    assert req.url.path == "/auth/v1/token"
    assert req.url.params["grant_type"] == "refresh_token"
    assert req.headers["apikey"] == auth.ANON_KEY
    assert json.loads(req.content) == {"refresh_token": "DUMMY-REFRESH-DO-NOT-LEAK"}
    assert auth.load_session()["refresh_token"] == "DUMMY-ROTATED"


async def test_a_rejected_refresh_is_actionable_and_does_not_echo(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(60))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": "invalid_grant", "echo": request.content.decode()}
        )

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    with pytest.raises(FetchError) as exc:
        await auth.session_cookie()
    message = str(exc.value)
    assert "400" in message
    assert "login chessreps" in message
    assert "DUMMY-REFRESH-DO-NOT-LEAK" not in message


async def test_a_connection_failure_does_not_leak_the_token(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(60))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    with pytest.raises(FetchError) as exc:
        await auth.session_cookie()
    assert "DUMMY-REFRESH-DO-NOT-LEAK" not in str(exc.value)


async def test_a_malformed_refresh_response_is_a_fetch_error(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_session(_fresh(60))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    with pytest.raises(FetchError) as exc:
        await auth.session_cookie()
    assert "login chessreps" in str(exc.value)
