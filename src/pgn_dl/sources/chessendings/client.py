"""Requests for chessendings pages, in English, as the signed-in account if any.

Every page is requested as its flight stream (``RSC: 1``; see ``flight.py``).
The server renders a page in the language the ``chessendings-locale`` cookie
names, or failing that the one ``Accept-Language`` asks for, so both are set
to English; otherwise the files would depend on the machine that downloaded
them. A page also says which language it was rendered in, and one in any
other is refused.

A signed-in request carries the stored Supabase session as its cookie (see
``auth.py``), fetched for each request, one request at a time, so a run that
outlasts the access token renews it once and saves the new refresh token
before any other request uses it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...core.fetch import Fetcher, FetchError
from . import auth, flight

ROOT = "https://chessendings.com"

#: The PGN ``Site`` header.
SITE = "chessendings.com"

LOCALE_COOKIE = "chessendings-locale=en"

#: Headers every page request carries besides its cookie.
PAGE_HEADERS = {"RSC": "1", "Accept-Language": "en"}


class Client:
    """One run's view of the site. ``signed_in`` sends the stored session."""

    def __init__(self, fetcher: Fetcher, *, signed_in: bool) -> None:
        self._fetcher = fetcher
        self.signed_in = signed_in
        self._renewing = asyncio.Lock()

    async def page(self, path: str) -> Any:
        """The page at ``path`` (``/train``, ``/practice/<slug>``, ...) as a tree."""
        headers = PAGE_HEADERS | {"Cookie": await self._cookie()}
        url = ROOT + path
        tree = flight.parse(await self._fetcher.get_text(url, headers=headers))
        provider = flight.props_with(tree, "locale", "source")
        if provider is not None and provider["locale"] != "en":
            raise FetchError(
                f"{url} came back in {provider['locale']!r} rather than English"
            )
        return tree

    async def _cookie(self) -> str:
        if not self.signed_in:
            return LOCALE_COOKIE
        # Two requests finding the token stale at once would both spend the
        # same refresh token, and the second exchange would fail.
        async with self._renewing:
            session = await auth.session_cookie()
        return f"{LOCALE_COOKIE}; {session}"
