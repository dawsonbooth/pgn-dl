"""chessable's own contract: schema and probe decoding. No network, no browser.

Storage invariants (permissions, symlink refusal, atomicity) are the credential
store's and are tested once in tests/core/test_credentials.py; chessable has no
refresh endpoint, so there is no refresh choreography to add on top.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

import pgn_dl.core.browser as browser_module
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessable import auth

# Invented throughout: nothing here is a real credential, and none of it is ever
# meant to reach a message.
DUMMY_JWT = "DUMMY-JWT-DO-NOT-LEAK"
DUMMY_UID = "424242"


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_real_sleep(fast_sign_in_poll: None) -> None:
    pass


def test_missing_credentials_tell_the_user_how_to_fix_it() -> None:
    with pytest.raises(FetchError) as exc:
        auth.load_credentials()
    assert "login chessable" in str(exc.value)


def test_saved_credentials_round_trip() -> None:
    auth.save_credentials(jwt=DUMMY_JWT, uid=DUMMY_UID)
    assert auth.load_credentials() == {"jwt": DUMMY_JWT, "uid": DUMMY_UID}


def test_credentials_without_a_jwt_are_refused() -> None:
    with pytest.raises(FetchError):
        auth.save_credentials(jwt="", uid=DUMMY_UID)
    with pytest.raises(FetchError):
        auth.save_credentials(jwt=DUMMY_UID, uid="")


def test_load_refuses_a_credential_file_missing_a_field() -> None:
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"jwt": "only-a-jwt"}', encoding="utf-8")
    with pytest.raises(FetchError) as exc:
        auth.load_credentials()
    assert "login chessable" in str(exc.value)


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


async def test_probe_delegates_to_read_credentials() -> None:
    page = FakePage(result={auth.JWT_KEY: DUMMY_JWT, auth.UID_KEY: DUMMY_UID})
    assert await auth._probe(None, page) == {"jwt": DUMMY_JWT, "uid": DUMMY_UID}


class FakeLoginPage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **kwargs: object) -> None:
        self.goto_calls.append(url)

    async def evaluate(self, script: str, arg: Any = None) -> dict[str, str]:
        return {auth.JWT_KEY: DUMMY_JWT, auth.UID_KEY: DUMMY_UID}


async def test_login_greets_with_the_capitalized_brand_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The registry name is lowercase ("chessable"), but the site's own name is
    # "Chessable"; the greeting must say what the site is actually called.
    page = FakeLoginPage()

    @asynccontextmanager
    async def fake_browser_context(profile: Path, *, headless: bool):
        class Ctx:
            async def new_page(self) -> FakeLoginPage:
                return page

        yield Ctx()

    monkeypatch.setattr(browser_module, "browser_context", fake_browser_context)
    await auth.login()
    assert "Sign in to Chessable;" in capsys.readouterr().out
