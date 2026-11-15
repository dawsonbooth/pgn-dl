"""Fixtures shared across the whole test tree."""

from __future__ import annotations

import os
from collections.abc import Callable
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
