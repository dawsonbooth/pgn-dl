"""Playwright-backed transport, for sources behind a bot challenge.

Uses the real installed Google Chrome (``channel="chrome"``) rather than
Playwright's bundled Chromium: the browser's cookies and TLS fingerprint are
what get the requests accepted.

Two shapes. :class:`BrowserFetcher` sends a headless context's requests with
the cookies a visible ``login`` left in the profile (chess.com).
:class:`PageWindow` reads a site's pages through a visible window, for a site
that serves them to no program and needs no sign-in to read (lichess.org); it
keeps Chrome's marks of automation, which the others take off.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from json import loads as json_loads
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .fetch import (
    Fatal,
    Fetcher,
    FetchError,
    check_response,
    not_json,
    transport_failure,
)
from .io import private_dir

_MISSING = (
    "This source needs a browser. Install the extra with:\n"
    "    uv sync --extra browser\n"
    "    uv run playwright install chrome"
)


def is_playwright_error(exc: BaseException) -> bool:
    """Whether ``exc`` is Playwright's own, rather than a bug in this code.

    Playwright is an optional extra, so its error type cannot be named at module
    scope. It is imported here instead, inside the handler for a call that has
    just failed through a Playwright context, where the package is installed by
    definition; an import that fails anyway answers "no", which re-raises the
    exception rather than burying it. Shared with the source login flows, which
    poll through Playwright's transient navigation errors the same way.
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
            if not is_playwright_error(exc):
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
            if not is_playwright_error(exc):
                raise
            raise transport_failure(exc) from None
        check_response(url, resp.status, resp.headers, text)
        return text

    async def post_text(
        self, url: str, *, headers: Mapping[str, str], body: str
    ) -> str:
        try:
            resp = await self._ctx.request.post(
                url, headers=dict(headers), data=body.encode()
            )
            text = await resp.text()
        except Exception as exc:
            if not is_playwright_error(exc):
                raise
            raise transport_failure(exc) from None
        check_response(url, resp.status, resp.headers, text)
        return text


#: The launch options that take the marks of automation off Chrome
#: (``navigator.webdriver``, the "controlled by automated test software" bar).
_UNMARKED: dict[str, Any] = {
    "args": ["--disable-blink-features=AutomationControlled"],
    "ignore_default_args": ["--enable-automation"],
}


@asynccontextmanager
async def browser_context(
    profile: Path, *, headless: bool, marked: bool = False
) -> AsyncIterator[Any]:
    """Yield a Chrome context backed by the persistent profile at ``profile``.

    Chrome starts without the marks of automation, as the sign-in windows and
    chess.com's transport always have, unless ``marked``: a window that must
    not disguise itself (``PageWindow``) keeps them.
    """
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
            viewport={"width": 1400, "height": 900},
            **({} if marked else _UNMARKED),
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


#: Runs in the window's tab: the page's own ``fetch``, so the request is
#: Chrome's, made the way the site's scripts make theirs. Given up after
#: ``wait`` milliseconds, body included, as a request would be.
_READ = """async ([url, wait]) => {
  const r = await fetch(url, {signal: AbortSignal.timeout(wait)});
  return [r.status, Object.fromEntries(r.headers.entries()), await r.text()];
}"""
_READ_WAIT = 60_000

#: Where the tab waits between reads: a document on the site's own origin, so
#: a read is same-origin, and one with no scripts, sockets or media to load.
_PARKING = "/robots.txt"

Launch = Callable[[Path], AbstractAsyncContextManager[Any]]


def _visible(profile: Path) -> AbstractAsyncContextManager[Any]:
    return browser_context(profile, headless=False, marked=True)


#: Why the run stops when the window goes away under it.
_CLOSED = "the Chrome window was closed"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class PageWindow:
    """A site's pages read through a visible Chrome window, signed out.

    Some sites (lichess.org) serve their pages only to a browser that is one,
    while their API stays open to programs. Headless Chrome names itself
    ``HeadlessChrome`` and is refused, and so is Playwright's request API,
    which is not Chrome's network stack at all; a visible Chrome is served like
    any visitor. So the window is shown, and nothing here disguises it: Chrome
    keeps the marks of automation, sends no User-Agent of pgn-dl's, and a
    challenge is never solved or waited out. A challenged read is
    :class:`~.fetch.Blocked`, and the runner backs off as it would for any.

    Chrome opens on the first read, not before, so a run that reads no page
    shows no window. The one tab parks on the site's ``robots.txt`` and reads
    each page with the page's own ``fetch``, which returns the page as served,
    before any of its scripts run. A tab that breaks is replaced at the next
    read. Chrome that will not start, or a window someone closes, is
    :class:`~.fetch.Fatal`: every later read would fail the same way, so the
    run stops.
    """

    def __init__(
        self,
        profile: Path,
        *,
        launch: Launch = _visible,
        on_open: Callable[[], None] | None = None,
    ) -> None:
        self._profile = profile
        self._launch = launch
        self._on_open = on_open
        self._stack = AsyncExitStack()
        self._ctx: Any = None
        self._page: Any = None
        self._parked: str | None = None  # the origin the tab is parked on
        self._closed = False  # Chrome went away under the run
        self._lock = asyncio.Lock()

    async def get_page(self, url: str) -> str:
        async with self._lock:
            try:
                page = await self._tab(_origin(url))
                status, headers, body = await page.evaluate(_READ, [url, _READ_WAIT])
            except Exception as exc:
                if not is_playwright_error(exc):
                    raise
                if self._closed:
                    raise Fatal(_CLOSED) from None
                # A tab that crashed, or that someone took to another page: the
                # next read starts from a new one.
                await self._drop_tab()
                raise transport_failure(exc) from None
        check_response(url, status, headers, body)
        return body

    async def _tab(self, origin: str) -> Any:
        """The tab, open and parked on ``origin``."""
        if self._ctx is None:
            self._ctx = await self._open()
        if self._closed or (self._page is not None and self._page.is_closed()):
            raise Fatal(_CLOSED)
        if self._page is None:
            self._page = await self._ctx.new_page()
            self._parked = None
        if self._parked != origin:
            parking = origin + _PARKING
            resp = await self._page.goto(
                parking, wait_until="domcontentloaded", timeout=60000
            )
            if resp is not None:
                try:
                    check_response(
                        parking, resp.status, resp.headers, await resp.text()
                    )
                except FetchError:
                    # Not left on screen, where a challenge's script would go
                    # on running.
                    await self._page.goto("about:blank")
                    raise
            self._parked = origin
        return self._page

    async def _open(self) -> Any:
        """Chrome, launched. A launch that fails would fail every read."""
        try:
            ctx = await self._stack.enter_async_context(self._launch(self._profile))
        except FetchError as exc:  # a profile directory refused
            raise Fatal(str(exc)) from None
        except Exception as exc:
            if not is_playwright_error(exc) and not isinstance(exc, RuntimeError):
                raise
            reason = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
            raise Fatal(f"Chrome would not start: {reason or type(exc).__name__}")
        ctx.on("close", lambda _: setattr(self, "_closed", True))
        # Said once Chrome is up: a refused profile opens no window.
        if self._on_open is not None:
            self._on_open()
        return ctx

    async def _drop_tab(self) -> None:
        page, self._page, self._parked = self._page, None, None
        if page is None or page.is_closed():
            return
        try:
            await page.close()
        except Exception as exc:
            if not is_playwright_error(exc):
                raise

    async def aclose(self) -> None:
        """Close Chrome, if a read ever opened it."""
        await self._stack.aclose()


class WindowedFetcher:
    """A :class:`~.fetch.PageFetcher`: pages through a window, the rest not.

    Requests to the site's API, and anywhere else, keep the transport the
    extractor would have had without the window.
    """

    def __init__(self, inner: Fetcher, window: PageWindow) -> None:
        self._inner = inner
        self._window = window

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        return await self._inner.post_json(url, headers=headers, json=json)

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        return await self._inner.get_text(url, headers=headers)

    async def post_text(
        self, url: str, *, headers: Mapping[str, str], body: str
    ) -> str:
        return await self._inner.post_text(url, headers=headers, body=body)

    async def get_page(self, url: str) -> str:
        return await self._window.get_page(url)


@asynccontextmanager
async def page_window(
    profile: Path, *, on_open: Callable[[], None] | None = None
) -> AsyncGenerator[PageWindow]:
    """A :class:`PageWindow` over ``profile``, closed when the run ends."""
    window = PageWindow(profile, on_open=on_open)
    try:
        yield window
    finally:
        await window.aclose()
