"""chess.com's login: the shared sign-in loop, with no credential to store.

chess.com has no credential file (see CONTEXT.md): its session lives in the
Chrome profile itself. Its login adopts the credential store's shared sign-in
loop only to detect that session, exiting automatically like every other
source instead of waiting for the user to come back and press Enter.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import pgn_dl.core.browser as browser_module
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chesscom import auth


class FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **kwargs: object) -> None:
        self.goto_calls.append(url)


class FakeCtx:
    def __init__(self, cookies: list[dict]) -> None:
        self._cookies = cookies
        self.page = FakePage()

    async def new_page(self) -> FakePage:
        return self.page

    async def cookies(self, url: str) -> list[dict]:
        return self._cookies


def _fake_browser_context(ctx: FakeCtx):
    @asynccontextmanager
    async def factory(profile: Path, *, headless: bool):
        yield ctx

    return factory


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_real_sleep(fast_sign_in_poll: None) -> None:
    pass


async def test_login_exits_once_the_session_cookie_appears(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = FakeCtx([{"name": "ACCESS_TOKEN", "value": "whatever-chess.com-set"}])
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context(ctx))

    await auth.login()  # must not raise, and must not hang waiting for Enter

    assert ctx.page.goto_calls == ["https://www.chess.com/login"]
    # Nothing of pgn-dl's own is written: the Chrome profile is the session.
    assert not (auth.profile_dir() / "credentials.json").exists()
    out = capsys.readouterr().out
    assert "Sign in to chess.com;" in out
    assert "chess.com session is saved in the Chrome profile" in out


async def test_login_gives_up_after_fifteen_minutes_without_the_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FakeCtx([])  # the session cookie never appears
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context(ctx))

    with pytest.raises(FetchError) as exc:
        await auth.login()
    message = str(exc.value)
    assert "15 minutes" in message
    assert "pgn-dl login chesscom" in message


async def test_login_ignores_an_unrelated_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FakeCtx([{"name": "some_other_cookie", "value": "x"}])
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context(ctx))

    with pytest.raises(FetchError):
        await auth.login()
