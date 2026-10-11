"""Connect-RPC client for chess.com's courses service.

The first call exchanges the ``ACCESS_TOKEN`` cookie for a Bearer JWT. The JWT
expires before a long course is through, and chess.com refuses a call bearing an
expired one with a 401, so a call refused with a 401 exchanges the cookie again and
is tried once more. The fetcher supplies the cookies.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ...core.fetch import Fetcher, FetchError

BASE = "https://www.chess.com/service/courses/rpc"
_AUTH_SVC = "chesscom.courses.v1.CoursesAuthenticationService"
_TRAIN_SVC = "chesscom.courses.v1.CoursesTrainingService"


class CoursesClient:
    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher
        self._jwt: str | None = None
        self._renewing = asyncio.Lock()
        self.session_id: str = str(uuid.uuid4())

    async def authenticate(self) -> None:
        """Exchange the ACCESS_TOKEN cookie for a Bearer JWT."""
        data = await self._post(
            _AUTH_SVC,
            "AuthenticateForCoursesWithCookie",
            {"providerName": "chesscom", "cookieName": "ACCESS_TOKEN"},
            None,
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
            {"variationId": variation_id, "learningSessionId": self.session_id},
        )

    async def _call(
        self, service: str, method: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        jwt = self._jwt
        if jwt is None:
            raise RuntimeError("call authenticate() first")
        try:
            return await self._post(service, method, body, jwt)
        except FetchError as exc:
            if exc.status != 401:
                raise
        # A 401 is read as an expired JWT. Of the calls refused with this JWT, whichever
        # takes the lock first renews it; the rest retry with what it got, or renew in
        # turn if it failed.
        async with self._renewing:
            if self._jwt == jwt:
                await self.authenticate()
        return await self._post(service, method, body, self._jwt)

    async def _post(
        self, service: str, method: str, body: dict[str, Any], jwt: str | None
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "connect-protocol-version": "1",
            "origin": "https://www.chess.com",
            "referer": "https://www.chess.com/courses",
        }
        if jwt is not None:
            headers["authorization"] = f"Bearer {jwt}"
        return await self._fetcher.post_json(
            f"{BASE}/{service}/{method}", headers=headers, json=body
        )
