"""aimchess's credential schema and sign-in probe. No network.

Storage invariants are the credential store's, tested in
tests/core/test_credentials.py.
"""

from __future__ import annotations

import base64
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

import pgn_dl.core.browser as browser_module
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.aimchess import auth

from .conftest import TOKEN


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_real_sleep(fast_sign_in_poll: None) -> None:
    pass


def test_a_missing_credential_says_how_to_sign_in() -> None:
    with pytest.raises(FetchError, match="login aimchess"):
        auth.load_token()


def test_a_saved_token_round_trips() -> None:
    auth.save_token(TOKEN)
    assert auth.load_token() == TOKEN


def test_a_credential_without_a_token_is_refused() -> None:
    with pytest.raises(FetchError):
        auth.save_token("")
    path = auth.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"jwt": "wrong-field"}', encoding="utf-8")
    with pytest.raises(FetchError, match="login aimchess") as exc:
        auth.load_token()
    assert "wrong-field" not in str(exc.value)


def _jwt(claims: dict[str, object]) -> str:
    """An unsigned token carrying ``claims``."""

    def part(obj: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part(claims)}.SIGNATURE"


@pytest.mark.parametrize(
    ("token", "expired"),
    [
        (_jwt({"exp": 1_000}), True),
        (_jwt({"exp": 2_000}), True),
        (_jwt({"exp": 3_000}), False),
        (_jwt({"user_id": 1}), False),
        ("not-a-jwt", False),
    ],
)
def test_is_expired(token: str, expired: bool) -> None:
    assert auth.is_expired(token, now=2_000) is expired


class FakePage:
    def __init__(
        self,
        *,
        url: str = "https://aimchess.com/home",
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.url = url
        self.result = result
        self.error = error
        self.visited: list[str] = []

    async def goto(self, url: str, **kwargs: object) -> None:
        self.visited.append(url)

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.parametrize(
    ("page", "found"),
    [
        (FakePage(result=json.dumps(TOKEN)), {"token": TOKEN}),
        (FakePage(result=None), None),
        (FakePage(result="not json"), None),
        (FakePage(result=json.dumps(_jwt({"exp": time.time() - 60}))), None),
        # "token" is a common key; a sign-in provider's must not be read.
        (FakePage(url="https://accounts.google.com/", result=json.dumps(TOKEN)), None),
        (FakePage(error=PlaywrightError("Execution context was destroyed")), None),
    ],
)
async def test_probe(page: FakePage, found: dict[str, str] | None) -> None:
    assert await auth._probe(None, page) == found


async def test_probe_raises_errors_that_are_not_from_playwright() -> None:
    with pytest.raises(TypeError):
        await auth._probe(None, FakePage(error=TypeError("bad call")))


async def test_login_signs_in_at_aimchess_and_saves_the_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(result=json.dumps(TOKEN))

    @asynccontextmanager
    async def fake_browser_context(profile: Path, *, headless: bool):
        class Ctx:
            async def new_page(self) -> FakePage:
                return page

        yield Ctx()

    monkeypatch.setattr(browser_module, "browser_context", fake_browser_context)
    await auth.login()
    assert page.visited == ["https://aimchess.com/auth/login"]
    out = capsys.readouterr().out
    assert "Sign in to Aimchess;" in out and TOKEN not in out
    assert auth.load_token() == TOKEN
