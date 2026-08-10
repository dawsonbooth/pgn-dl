"""Credential storage for the chessable source. No network, no browser.

Chessable's credential is a localStorage JWT (plus the account's user id), read
out of the browser once at login and stored owner-only. There is no refresh
flow, so these tests cover only the store: shape, permissions, and the rule that
no message ever carries the token.
"""

from __future__ import annotations

import stat
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from pgn_dl.core import io
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessable import auth

# Invented throughout: nothing here is a real credential, and none of it is ever
# meant to reach a message.
DUMMY_JWT = "DUMMY-JWT-DO-NOT-LEAK"
DUMMY_UID = "424242"


def test_missing_credentials_tell_the_user_how_to_fix_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    with pytest.raises(FetchError) as exc:
        auth.load_credentials()
    assert "login chessable" in str(exc.value)


def test_saved_credentials_round_trip_owner_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    auth.save_credentials(jwt=DUMMY_JWT, uid=DUMMY_UID)
    assert auth.load_credentials() == {"jwt": DUMMY_JWT, "uid": DUMMY_UID}
    mode = stat.S_IMODE(auth.credentials_path().stat().st_mode)
    assert mode == 0o600, f"credentials must be owner-only, got {mode:o}"


def test_saving_narrows_a_pre_existing_world_readable_file(
    tmp_path, monkeypatch
) -> None:
    # write_text_atomic replaces via an owner-only temp file, so a file restored
    # from a backup at 0644 must not keep a long-lived token world-readable.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    auth.save_credentials(jwt=DUMMY_JWT, uid=DUMMY_UID)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"a pre-existing file must be narrowed, got {mode:o}"


def test_credentials_without_a_jwt_are_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    with pytest.raises(FetchError):
        auth.save_credentials(jwt="", uid=DUMMY_UID)
    with pytest.raises(FetchError):
        auth.save_credentials(jwt=DUMMY_UID, uid="")


def test_load_refuses_a_credential_file_missing_a_field(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"jwt": "only-a-jwt"}', encoding="utf-8")
    with pytest.raises(FetchError) as exc:
        auth.load_credentials()
    assert "login chessable" in str(exc.value)


def test_a_symlinked_credentials_file_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path / "profile"))
    victim = tmp_path / "victim"
    victim.write_text("not a credential", encoding="utf-8")
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(victim)
    with pytest.raises(FetchError):
        auth.save_credentials(jwt=DUMMY_JWT, uid=DUMMY_UID)
    assert victim.read_text(encoding="utf-8") == "not a credential"


def test_a_failure_to_save_does_not_echo_the_token(tmp_path, monkeypatch) -> None:
    # The save message reaches the terminal and may reach a log; the JWT must
    # never ride along in it.
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(io.os, "replace", no_space)
    with pytest.raises(FetchError) as exc:
        auth.save_credentials(jwt=DUMMY_JWT, uid=DUMMY_UID)
    assert DUMMY_JWT not in str(exc.value)


def test_localstorage_keys_are_the_production_ones() -> None:
    # Pin the exact keys the login flow reads out of the browser; a Chessable
    # rename would break login, and this says where to look.
    assert auth.JWT_KEY == "chessable.web.production.JWT"
    assert auth.UID_KEY == "chessable.web.production.UID"


class FakePage:
    """A stand-in for a Playwright page whose evaluate returns or raises."""

    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls = 0

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


async def test_read_credentials_returns_the_stored_session() -> None:
    page = FakePage(result={auth.JWT_KEY: DUMMY_JWT, auth.UID_KEY: DUMMY_UID})
    assert await auth._read_credentials(page) == {"jwt": DUMMY_JWT, "uid": DUMMY_UID}


async def test_read_credentials_is_none_before_sign_in() -> None:
    page = FakePage(result={auth.JWT_KEY: None, auth.UID_KEY: None})
    assert await auth._read_credentials(page) is None


async def test_read_credentials_polls_through_a_navigation_race() -> None:
    # The reported bug: during the chess.com OAuth hand-off the page navigates,
    # and an evaluate that lands mid-navigation raises "Execution context was
    # destroyed". That is a not-signed-in-yet state, so it must not crash login.
    page = FakePage(
        error=PlaywrightError(
            "Execution context was destroyed, most likely because of a navigation"
        )
    )
    assert await auth._read_credentials(page) is None


async def test_read_credentials_does_not_swallow_a_bug() -> None:
    # A defect of ours (wrong evaluate signature, say) is not a transient browser
    # state; it must surface, not be polled over for 15 minutes.
    page = FakePage(error=TypeError("evaluate expected a string"))
    with pytest.raises(TypeError):
        await auth._read_credentials(page)
