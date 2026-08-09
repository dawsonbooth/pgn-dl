"""Playwright-backed transport, for sources behind a bot challenge.

Uses the real installed Google Chrome (``channel="chrome"``) rather than
Playwright's bundled Chromium: the browser's cookies and TLS fingerprint are
what get the requests accepted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from json import loads as json_loads
from pathlib import Path
from typing import Any

from .fetch import check_response, not_json, transport_failure
from .io import private_dir

_MISSING = (
    "This source needs a browser. Install the extra with:\n"
    "    uv sync --extra browser\n"
    "    uv run playwright install chrome"
)


def _is_playwright_error(exc: BaseException) -> bool:
    """Whether ``exc`` is Playwright's own, rather than a bug in this code.

    Playwright is an optional extra, so its error type cannot be named at module
    scope. It is imported here instead, inside the handler for a call that has
    just failed through a Playwright context, where the package is installed by
    definition; an import that fails anyway answers "no", which re-raises the
    exception rather than burying it.
    """
    try:
        from playwright.async_api import Error
    except ImportError:  # pragma: no cover - depends on install extras
        return False
    return isinstance(exc, Error)


class BrowserFetcher:
    """Requests issued through a Playwright context, inheriting its cookies.

    Each request is wrapped the way :class:`~.fetch.HttpxFetcher` wraps httpx's,
    so a reset connection, a closed context or a timeout reaches the runner as a
    :class:`~.fetch.FetchError`. Anything that is not Playwright's own error is
    a defect here rather than a transport failure, and is left to surface as the
    traceback it is.
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        try:
            resp = await self._ctx.request.post(url, headers=dict(headers), data=json)
            text = await resp.text()
        except Exception as exc:
            if not _is_playwright_error(exc):
                raise
            raise transport_failure(exc) from None
        # Checked on every response, not only failures: Cloudflare can serve a
        # challenge with a 2xx status (marked by a cf-mitigated header), and
        # both fetchers must give the runner the same answer.
        check_response(url, resp.status, resp.headers, text)
        # Decoded from the body already in hand rather than by asking Playwright
        # for it again, so a malformed body is the only thing this can raise.
        try:
            return json_loads(text)
        except ValueError:
            raise not_json(url, resp.status) from None

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        try:
            resp = await self._ctx.request.get(url, headers=dict(headers))
            text = await resp.text()
        except Exception as exc:
            if not _is_playwright_error(exc):
                raise
            raise transport_failure(exc) from None
        check_response(url, resp.status, resp.headers, text)
        return text


@asynccontextmanager
async def browser_context(profile: Path, *, headless: bool) -> AsyncIterator[Any]:
    """Yield a Chrome context backed by the persistent profile at ``profile``."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(_MISSING) from exc

    # Owner-only: the profile is a signed-in session.
    private_dir(profile)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1400, "height": 900},
        )
        try:
            yield ctx
        finally:
            await ctx.close()


@asynccontextmanager
async def browser_fetcher(profile: Path) -> AsyncIterator[BrowserFetcher]:
    """A fetcher over a headless context. Only ``login`` shows a window."""
    async with browser_context(profile, headless=True) as ctx:
        yield BrowserFetcher(ctx)
