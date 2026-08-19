"""noctie's own contract: schema, probe decoding, and refresh choreography.

Storage invariants (permissions, symlink refusal, atomicity) and the refresh
call's error taxonomy are the credential store's and are tested once in
tests/core/test_credentials.py; this suite covers only what noctie adds on top.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.noctie import auth

# Invented throughout: nothing here is a credential, and none of it is ever
# meant to reach a message.
DUMMY = "DUMMY-REFRESH-DO-NOT-LEAK"


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


def test_the_profile_path_expands_a_tilde_and_falls_back_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Anything that does not expand the value (a compose file, a systemd unit,
    # a quoted export) otherwise gets a directory literally named "~".
    monkeypatch.setenv("PGN_DL_PROFILE", "~/pgn-dl-profile")
    assert auth.profile_dir() == (Path.home() / "pgn-dl-profile").resolve() / "noctie"
    monkeypatch.setenv("PGN_DL_PROFILE", "")
    assert auth.profile_dir() == Path(".profile").resolve() / "noctie"


async def test_id_token_exchange_persists_the_rotated_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


async def test_a_rejected_refresh_token_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth.save_refresh_token("STALE")

    async def fake_exchange(refresh_token: str) -> dict:
        raise FetchError("securetoken failed: 400 TOKEN_EXPIRED")

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert "login noctie" in str(exc.value)


async def test_the_login_hint_is_not_repeated() -> None:
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert str(exc.value).count("login noctie") == 1, str(exc.value)


async def test_a_response_without_an_id_token_is_a_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth.save_refresh_token("OLD")

    async def fake_exchange(refresh_token: str) -> dict:
        return {"refresh_token": "ROTATED", "expires_in": "3600"}

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert "ID token" in str(exc.value)


async def test_a_failure_to_persist_the_rotated_token_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unhandled, this leaves the runner with a traceback whose frame holds the
    # rotated refresh token.
    from pgn_dl.core import io as io_module

    auth.save_refresh_token("DUMMY-OLD")

    async def fake_exchange(refresh_token: str) -> dict:
        return {"id_token": "ID-1", "refresh_token": "DUMMY-ROTATED"}

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(auth, "_exchange", fake_exchange)
    monkeypatch.setattr(io_module.os, "replace", no_space)

    with pytest.raises(FetchError) as exc:
        await auth.id_token()
    assert "DUMMY-ROTATED" not in str(exc.value)
    assert "login noctie" in str(exc.value)


async def test_a_real_exchange_reads_both_tokens_back(
    monkeypatch: pytest.MonkeyPatch, mock_httpx_client: Callable[..., Any]
) -> None:
    auth.save_refresh_token(DUMMY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"id_token": "ID-1", "refresh_token": "DUMMY-ROTATED"}
        )

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    assert await auth.id_token() == "ID-1"
    assert seen[0].url.host == "securetoken.googleapis.com"
    assert b"grant_type=refresh_token" in seen[0].content
    assert auth.load_refresh_token() == "DUMMY-ROTATED"


class FakePage:
    def __init__(self, *, url: str, entries: list[dict] | None = None) -> None:
        self.url = url
        self._entries = entries or []

    async def evaluate(self, script: str) -> list[dict]:
        return self._entries


async def test_probe_skips_while_still_on_the_login_page() -> None:
    page = FakePage(url="https://app.noctie.ai/login")
    assert await auth._probe(None, page) is None


async def test_probe_extracts_the_refresh_token_from_indexeddb() -> None:
    page = FakePage(
        url="https://app.noctie.ai/themes",
        entries=[{"value": {"stsTokenManager": {"refreshToken": "IDB-TOKEN"}}}],
    )
    assert await auth._probe(None, page) == {"refresh_token": "IDB-TOKEN"}


async def test_probe_returns_none_before_the_token_is_written() -> None:
    page = FakePage(url="https://app.noctie.ai/themes", entries=[])
    assert await auth._probe(None, page) is None


async def test_probe_ignores_an_entry_with_no_token_manager() -> None:
    page = FakePage(
        url="https://app.noctie.ai/themes",
        entries=[{"value": {"somethingElse": True}}],
    )
    assert await auth._probe(None, page) is None
