"""Connect-RPC client for chess.com's courses service.

Calls go through Playwright's :class:`APIRequestContext`, which inherits cookies
from the persistent profile. The first call exchanges the ``ACCESS_TOKEN``
cookie for a Bearer JWT; that JWT is reused for the rest of the run.
"""

from __future__ import annotations

import uuid
from typing import Any

from playwright.async_api import BrowserContext


class CloudflareBlocked(RuntimeError):
    """Raised when chess.com returns a Cloudflare interstitial instead of JSON."""


BASE = "https://www.chess.com/service/courses/rpc"
_AUTH_SVC = "chesscom.courses.v1.CoursesAuthenticationService"
_TRAIN_SVC = "chesscom.courses.v1.CoursesTrainingService"

# Cloudflare serves its interstitial/challenge pages under these statuses, with
# one of these markers in the body. Matching broadly (rather than only a 403
# "Just a moment" page) lets the scraper back off and resume on a challenge
# instead of treating it as a fatal error.
_CF_STATUSES = frozenset({403, 429, 503})
_CF_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required",
    "enable javascript and cookies",
)


def _is_cloudflare_challenge(status: int, headers: dict[str, str], body: str) -> bool:
    if headers.get("cf-mitigated"):
        return True
    return status in _CF_STATUSES and any(m in body.lower() for m in _CF_MARKERS)


class CoursesClient:
    def __init__(self, ctx: BrowserContext) -> None:
        self._ctx = ctx
        self._jwt: str | None = None
        self.session_id: str = str(uuid.uuid4())

    async def authenticate(self) -> None:
        """Exchange the ACCESS_TOKEN cookie for a Bearer JWT."""
        data = await self._call(
            _AUTH_SVC,
            "AuthenticateForCoursesWithCookie",
            {"providerName": "chesscom", "cookieName": "ACCESS_TOKEN"},
            authed=False,
        )
        self._jwt = data["accessToken"]

    async def get_learning_session(self, course_id: str) -> dict[str, Any]:
        """Return the course outline (chapters + every lesson header)."""
        return await self._call(
            _TRAIN_SVC,
            "GetCourseLearningSession",
            {
                "contextType": "moveTrainer",
                "contextId": course_id,
                "learningSessionId": self.session_id,
            },
        )

    async def get_lesson(self, variation_id: str) -> dict[str, Any]:
        """Return one lesson's full content (plies + comments)."""
        return await self._call(
            _TRAIN_SVC,
            "GetCourseTrainingForLearning",
            {
                "variationId": variation_id,
                "learningSessionId": self.session_id,
            },
        )

    async def _call(
        self,
        service: str,
        method: str,
        body: dict[str, Any],
        *,
        authed: bool = True,
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "connect-protocol-version": "1",
            "origin": "https://www.chess.com",
            "referer": "https://www.chess.com/courses",
        }
        if authed:
            if self._jwt is None:
                raise RuntimeError("call authenticate() first")
            headers["authorization"] = f"Bearer {self._jwt}"

        url = f"{BASE}/{service}/{method}"
        resp = await self._ctx.request.post(url, headers=headers, data=body)
        if not resp.ok:
            text = await resp.text()
            if _is_cloudflare_challenge(resp.status, resp.headers, text):
                raise CloudflareBlocked(f"{service}/{method}: Cloudflare challenge")
            raise RuntimeError(f"{service}/{method} failed: {resp.status} {text[:500]}")
        return await resp.json()
