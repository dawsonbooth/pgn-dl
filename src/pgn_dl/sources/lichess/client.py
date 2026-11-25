"""Requests to Lichess: its API, its asset files, and the opening explorer.

Lichess asks API clients to send one request at a time, to name themselves in
the User-Agent, and after a 429 to wait a full minute before asking again.
Every request of a run goes through one :class:`Client`, which holds a lock
across each request so concurrent items queue rather than overlap. After a
429 no request goes out until the minute is over, whichever item it is for:
the one refused is asked once more, and a second 429 in a row reaches the
runner as the block it is, and the runner backs off and eventually stops. A
bot challenge is never waited out, whatever its status.

The site's own pages are served only to a browser. Wherever the documented
API, the JSON lichess.org answers for a study, the static files every page
loads, or the source Lichess publishes cover something, that is where it comes
from. The one thing they do not, the opening pages' descriptions, is read from
the page through a visible, signed-out Chrome window (``core/browser.py``'s
``PageWindow``), under the same lock.

A request carries the saved token only when it asks to (``auth=True``), so the
token never reaches the asset host or GitHub.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from ...core.fetch import Blocked, Fetcher, PageFetcher

ROOT = "https://lichess.org"
EXPLORER = "https://explorer.lichess.org"
ASSETS = "https://lichess1.org/assets/compiled"

#: How Lichess asks to be told who is calling: a name and where to find it.
USER_AGENT = "pgn-dl (+https://github.com/dawsonbooth/pgn-dl)"

#: Seconds to wait after a 429, as Lichess's API documentation asks.
RATE_LIMIT_WAIT = 60.0


class Client:
    """One run's requests to Lichess, one at a time.

    ``token`` is the saved API token, or None for a run that sends none.
    """

    def __init__(self, fetcher: Fetcher, *, token: str | None = None) -> None:
        self._fetcher = fetcher
        self._token = token
        self._lock = asyncio.Lock()
        self._resume_at = 0.0  # when the last 429's wait is over

    @property
    def signed_in(self) -> bool:
        return self._token is not None

    async def text(
        self, url: str, *, accept: str | None = None, auth: bool = False
    ) -> str:
        """GET ``url`` as text."""

        def request(headers: dict[str, str]) -> Awaitable[str]:
            return self._fetcher.get_text(url, headers=headers)

        return await self._send(request, accept=accept, auth=auth)

    async def json(self, url: str, *, auth: bool = False) -> Any:
        """GET ``url`` and decode it as JSON.

        A body that will not decode raises ``ValueError``, which the runner
        reports as a response it could not read.
        """
        return json.loads(await self.text(url, accept="application/json", auth=auth))

    async def post_text(
        self, url: str, body: str, *, accept: str | None = None, auth: bool = False
    ) -> str:
        """POST ``body`` as plain text to ``url`` and return the response text."""

        def request(headers: dict[str, str]) -> Awaitable[str]:
            headers = headers | {"Content-Type": "text/plain"}
            return self._fetcher.post_text(url, headers=headers, body=body)

        return await self._send(request, accept=accept, auth=auth)

    async def page(
        self,
        url: str,
        *,
        rate_limit_wait: float = RATE_LIMIT_WAIT,
        turn: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        """One of lichess.org's own pages, as a signed-out visitor is served it.

        Needs the window an extractor gets by setting ``reads_pages``. The
        page goes to the window's browser, which sends neither the token nor
        a User-Agent of pgn-dl's: it is the browser asking, as itself.
        ``rate_limit_wait`` is how long a 429 is waited out, for pages whose
        limit runs longer than the API's minute. ``turn``, a pace, is awaited
        just before the page goes out, so a pace counts from pages sent rather
        than from turns handed out while a wait held everything up.
        """
        fetcher = self._fetcher
        if not isinstance(fetcher, PageFetcher):
            raise TypeError(
                "reading a Lichess page needs a window; the extractor must set "
                "reads_pages"
            )

        def request(headers: dict[str, str]) -> Awaitable[str]:
            return fetcher.get_page(url)

        return await self._send(
            request, accept=None, auth=False, wait=rate_limit_wait, before=turn
        )

    async def _send(
        self,
        request: Callable[[dict[str, str]], Awaitable[str]],
        *,
        accept: str | None,
        auth: bool,
        wait: float = RATE_LIMIT_WAIT,
        before: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        headers = {"User-Agent": USER_AGENT}
        if accept is not None:
            headers["Accept"] = accept
        if auth and self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        async with self._lock:
            retried = False
            while True:
                if (left := self._resume_at - monotonic()) > 0:
                    await asyncio.sleep(left)
                if before is not None:
                    await before()
                try:
                    return await request(headers)
                except Blocked as exc:
                    if exc.challenge or exc.status != 429:
                        raise  # waiting is not the remedy for a challenge
                    self._resume_at = monotonic() + wait
                    if retried:
                        raise
                    retried = True
