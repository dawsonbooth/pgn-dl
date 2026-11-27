"""Client for aimchess's REST API, under https://aimchess.com/api/.

Requests carry the stored JWT as ``Authorization: JWT <token>``, as the web app
sends it. The API has no bot protection in front of it.
"""

from __future__ import annotations

import json
from typing import Any

from ...core.fetch import Blocked, Fetcher, FetchError

BASE = "https://aimchess.com/api"


class DailyLimitReached(FetchError):
    """aimchess refused a lesson because the free plan's daily limit is used up."""


class Client:
    def __init__(self, fetcher: Fetcher, *, token: str) -> None:
        self._fetcher = fetcher
        self._headers = {"Authorization": f"JWT {token}", "Accept": "application/json"}

    async def explore_listing(self) -> dict[str, Any]:
        """Every Explore topic and lesson, in the site's order."""
        return await self._get_json("explore/lessons/")

    async def checkmate_patterns(self) -> dict[str, Any]:
        """The Checkmate Patterns module's flashcards."""
        return await self._get_json("modules/checkmate_flashcards/")

    async def explore_lesson(self, lesson_id: str) -> Any:
        """One Explore lesson with its steps.

        The endpoint answers only POST, the request that opens a lesson: it
        marks the lesson started and, on the free plan, counts it toward the
        daily limit. Past the limit it answers 403.
        """
        url = f"{BASE}/explore/lessons/{lesson_id}/"
        try:
            return await self._fetcher.post_json(url, headers=self._headers, json=None)
        except Blocked:
            raise  # a bot challenge can be a 403 too; the runner retries those
        except FetchError as exc:
            if exc.status != 403:
                raise
            raise DailyLimitReached(
                f"{exc}; the free plan's daily Explore limit is used up. "
                "Re-run tomorrow.",
                status=403,
            ) from None

    async def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{BASE}/{path}"
        text = await self._fetcher.get_text(url, headers=self._headers)
        try:
            data = json.loads(text)
        except ValueError:
            # The body is left out: it may be a sign-in page, and the request
            # carried the token.
            raise FetchError(f"{url}: response body is not JSON") from None
        if not isinstance(data, dict):
            raise FetchError(f"{url}: response is not a JSON object")
        return data
