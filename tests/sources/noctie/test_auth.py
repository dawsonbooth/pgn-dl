"""Credential storage and token refresh. No network, no browser."""

from __future__ import annotations

import errno
import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pgn_dl.core import io
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.noctie import auth

# Invented throughout: nothing here is a credential, and none of it is ever
# meant to reach a message.
DUMMY = "DUMMY-REFRESH-DO-NOT-LEAK"


def test_missing_credentials_tell_the_user_how_to_fix_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    with pytest.raises(FetchError) as exc:
        auth.load_refresh_token()
    assert "login noctie" in str(exc.value)


def test_saved_credentials_are_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("SECRET-REFRESH")
    p = auth.credentials_path()
    assert auth.load_refresh_token() == "SECRET-REFRESH"
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"credentials must be owner-only, got {mode:o}"


def test_saving_narrows_a_pre_existing_world_readable_file(tmp_path, monkeypatch) -> None:
    # os.open's mode applies only on creation, so a file restored from a backup
    # or copied between machines could otherwise keep 0644 and hold a
    # long-lived credential world-readable.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    auth.save_refresh_token("SECRET-REFRESH")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"a pre-existing file must be narrowed, got {mode:o}"


def test_the_profile_directory_is_owner_only(tmp_path, monkeypatch) -> None:
    # The same directory is Chrome's user_data_dir, so a second copy of the
    # token lives underneath it in IndexedDB.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    d = auth.profile_dir()
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o755)
    auth.save_refresh_token("SECRET-REFRESH")
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode == 0o700, f"profile dir must be owner-only, got {mode:o}"


def test_saving_twice_replaces_rather_than_appends(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("FIRST")
    auth.save_refresh_token("SECOND")
    assert auth.load_refresh_token() == "SECOND"
    assert json.loads(auth.credentials_path().read_text())["refresh_token"] == "SECOND"


async def test_id_token_exchange_persists_the_rotated_refresh_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("OLD")
    calls = []

    async def fake_exchange(refresh_token: str) -> dict:
        calls.append(refresh_token)
        return {"id_token": "ID-1", "refresh_token": "ROTATED", "expires_in": "3600"}

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    assert await auth.id_token() == "ID-1"
    assert calls == ["OLD"]
    # The rotated token must be what a later run uses.
    assert auth.load_refresh_token() == "ROTATED"


async def test_a_rejected_refresh_token_is_actionable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("STALE")

    async def fake_exchange(refresh_token: str) -> dict:
        raise FetchError("securetoken failed: 400 TOKEN_EXPIRED")

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert "login noctie" in str(exc.value)


async def test_the_login_hint_is_not_repeated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert str(exc.value).count("login noctie") == 1, str(exc.value)


async def test_a_connection_failure_is_a_fetch_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("SECRET-REFRESH")

    class FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def post(self, url, data=None):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    # A transport failure must not leak the credential it was carrying.
    assert "SECRET-REFRESH" not in str(exc.value)


async def test_a_response_without_an_id_token_is_a_fetch_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("OLD")

    async def fake_exchange(refresh_token: str) -> dict:
        return {"refresh_token": "ROTATED", "expires_in": "3600"}

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert "ID token" in str(exc.value)


def test_the_profile_path_expands_a_tilde_and_falls_back_when_unset(monkeypatch) -> None:
    # Anything that does not expand the value (a compose file, a systemd unit,
    # a quoted export) otherwise gets a directory literally named "~".
    monkeypatch.setenv("PGN_DL_PROFILE", "~/pgn-dl-profile")
    assert auth.profile_dir() == (Path.home() / "pgn-dl-profile").resolve() / "noctie"
    monkeypatch.setenv("PGN_DL_PROFILE", "")
    assert auth.profile_dir() == Path(".profile").resolve() / "noctie"


def test_a_symlinked_credentials_file_is_refused(tmp_path, monkeypatch) -> None:
    # The profile directory is owner-only, so a planted link is how a token gets
    # written outside it, and the chmod that follows lands on the target.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path / "profile"))
    victim = tmp_path / "victim"
    victim.write_text("not a credential", encoding="utf-8")
    victim.chmod(0o644)
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(victim)

    with pytest.raises(FetchError):
        auth.save_refresh_token(DUMMY)

    assert victim.read_text(encoding="utf-8") == "not a credential"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_a_symlinked_profile_directory_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path / "profile"))
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    d = auth.profile_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.symlink_to(outside, target_is_directory=True)

    with pytest.raises(FetchError):
        auth.save_refresh_token(DUMMY)

    assert not (outside / "credentials.json").exists()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755


def _no_space(*args: object, **kwargs: object) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


def test_a_save_that_fails_partway_leaves_the_stored_credential_intact(
    tmp_path, monkeypatch
) -> None:
    # A truncated or empty credentials.json locks the user out until they sign
    # in again, so the replacement has to be all or nothing.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("DUMMY-FIRST")
    monkeypatch.setattr(io.os, "replace", _no_space)

    with pytest.raises(FetchError):
        auth.save_refresh_token("DUMMY-SECOND")

    assert auth.load_refresh_token() == "DUMMY-FIRST"
    assert not list(auth.credentials_path().parent.glob("*.tmp"))


async def test_a_failure_to_persist_the_rotated_token_is_reported_not_raised(
    tmp_path, monkeypatch
) -> None:
    # Unhandled, this leaves the runner with a traceback whose frame holds the
    # rotated refresh token.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token("DUMMY-OLD")

    async def fake_exchange(refresh_token: str) -> dict:
        return {"id_token": "ID-1", "refresh_token": "DUMMY-ROTATED"}

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    monkeypatch.setattr(io.os, "replace", _no_space)

    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert "DUMMY-ROTATED" not in str(exc.value)
    assert "login noctie" in str(exc.value)


Handler = Callable[[httpx.Request], httpx.Response]


def _mock_client(handler: Handler) -> Callable[..., httpx.AsyncClient]:
    """An ``httpx.AsyncClient`` factory answering from ``handler``, opening no socket."""
    real = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    return factory


async def test_a_real_exchange_reads_both_tokens_back(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token(DUMMY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id_token": "ID-1", "refresh_token": "DUMMY-ROTATED"})

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    assert await auth.id_token() == "ID-1"
    assert seen[0].url.host == "securetoken.googleapis.com"
    assert b"grant_type=refresh_token" in seen[0].content
    assert auth.load_refresh_token() == "DUMMY-ROTATED"


async def test_a_rejected_exchange_does_not_echo_the_credential_back(tmp_path, monkeypatch) -> None:
    # securetoken repeats the request in its error body, so the body is the
    # refresh token.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token(DUMMY)

    def handler(request: httpx.Request) -> httpx.Response:
        assert DUMMY.encode() in request.content
        return httpx.Response(
            400,
            json={"error": {"message": "TOKEN_EXPIRED", "request": request.content.decode()}},
        )

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert DUMMY not in str(exc.value)
    assert "400" in str(exc.value)


async def test_a_malformed_success_does_not_echo_the_credential_back(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_refresh_token(DUMMY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"<html>proxy could not send {request.content.decode()}")

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(handler))
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert DUMMY not in str(exc.value)
    assert "malformed" in str(exc.value)
