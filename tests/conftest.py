"""Fixtures shared across the whole test tree."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

Handler = Callable[[httpx.Request], httpx.Response]

#: For tests that assert POSIX permission bits. Windows has none: chmod only
#: sets or clears read-only, and stat reports every directory as 0777.
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


@pytest.fixture
def symlink() -> Callable[[Path, Path], None]:
    """Make a symlink, skipping the test if Windows refuses.

    Windows allows symlinks only with Developer Mode on or as an administrator.
    """

    def make(link: Path, target: Path) -> None:
        try:
            link.symlink_to(target)
        except OSError as exc:
            if os.name != "nt":
                raise
            pytest.skip(f"cannot create a symlink here: {exc.strerror}")

    return make


@pytest.fixture
def mock_httpx_client() -> Callable[[Handler], Callable[..., httpx.AsyncClient]]:
    """An ``httpx.AsyncClient`` factory answering from a handler, opening no socket.

    Assign the result to ``httpx.AsyncClient`` via ``monkeypatch.setattr`` to
    make every client built inside the code under test route through it.
    """

    def make(handler: Handler) -> Callable[..., httpx.AsyncClient]:
        real = httpx.AsyncClient

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            return real(transport=httpx.MockTransport(handler), **kwargs)

        return factory

    return make


@pytest.fixture
def fast_sign_in_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the sign-in loop's 3-second poll interval so tests run instantly.

    Not autouse: it patches the real ``asyncio.sleep``, and other suites (the
    runner's backoff, for one) rely on that being real. Opt in per test file
    with a local ``@pytest.fixture(autouse=True)`` that depends on this one.
    """
    from pgn_dl.core import credentials as credentials_module

    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(credentials_module.asyncio, "sleep", fast_sleep)


#: A Supabase session as a site's auth cookie carries it. Invented: nothing here
#: is a credential, and none of it may reach a message.
SUPABASE_SESSION = {
    "access_token": "DUMMY-ACCESS-DO-NOT-LEAK",
    "refresh_token": "DUMMY-REFRESH-DO-NOT-LEAK",
    "expires_at": 4102444800,  # 2100-01-01, far future
    "token_type": "bearer",
    "user": {"id": "u-1"},
}


def supabase_cookie(session: dict) -> str:
    """``session`` as @supabase/ssr writes it into its cookie."""
    raw = json.dumps(session).encode()
    return "base64-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


class SignInBrowser:
    """A browser for the sign-in loop: it opens pages and serves cookies."""

    def __init__(self) -> None:
        self.cookies: list[dict] = []
        self.visited: list[str] = []
        self.asked: list[str] = []


@pytest.fixture
def sign_in_browser(
    monkeypatch: pytest.MonkeyPatch, fast_sign_in_poll: None
) -> SignInBrowser:
    """Stand a fake browser in for the real one during ``login``."""
    import pgn_dl.core.browser as browser_module

    browser = SignInBrowser()

    class Page:
        async def goto(self, url: str, **kwargs: object) -> None:
            browser.visited.append(url)

    class Context:
        async def new_page(self) -> Page:
            return Page()

        async def cookies(self, url: str) -> list[dict]:
            browser.asked.append(url)
            return browser.cookies

    @asynccontextmanager
    async def fake_browser_context(profile: Path, *, headless: bool):
        yield Context()

    monkeypatch.setattr(browser_module, "browser_context", fake_browser_context)
    return browser
