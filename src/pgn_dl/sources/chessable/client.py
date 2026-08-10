"""HTTP client for Chessable's internal ``/api`` JSON service.

Every request carries the stored JWT as a Bearer token and a desktop-Chrome
``User-Agent``: Chessable sits behind Cloudflare, which 403s the default httpx
User-Agent, and the API itself needs the token. The uid rides on the query
string, as Chessable's own client sends it; the JWT stays in a header so it
never lands in a URL, a log, or a referer.
"""

from __future__ import annotations

import json
from typing import Any

from ...core.fetch import Fetcher, FetchError

BASE = "https://www.chessable.com/api/v1"

#: A real desktop-Chrome UA. Cloudflare admits this where it blocks httpx's own.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class GameClient:
    def __init__(self, fetcher: Fetcher, *, jwt: str, uid: str) -> None:
        self._fetcher = fetcher
        self._jwt = jwt
        self._uid = uid

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._jwt}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.chessable.com/",
        }

    async def get_course(self, bid: str) -> dict[str, Any]:
        """The course outline (chapters + every variation header)."""
        url = f"{BASE}/getCourse?uid={self._uid}&bid={bid}&includeVariations=true"
        return await self._get_json(url)

    async def get_game(self, bid: str, oid: str) -> dict[str, Any]:
        """One variation's full content (moves, comments, drawings)."""
        url = f"{BASE}/getGame?lng=en&uid={self._uid}&oid={oid}&cid={bid}"
        return await self._get_json(url)

    async def _get_json(self, url: str) -> dict[str, Any]:
        text = await self._fetcher.get_text(url, headers=self._headers())
        try:
            data = json.loads(text)
        except ValueError:
            # The body stays out of the message: a Cloudflare or sign-in page
            # arrives here as HTML, and this request carried the credential.
            raise FetchError(f"{url}: response body is not JSON") from None
        if not isinstance(data, dict):
            raise FetchError(f"{url}: response was not a JSON object")
        return data
