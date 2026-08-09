"""Connect-RPC client for chess.com's courses service.

The first call exchanges the ``ACCESS_TOKEN`` cookie for a Bearer JWT; that JWT
is reused for the rest of the run. The fetcher supplies the cookies.
"""

from __future__ import annotations

import uuid
from typing import Any

from ...core.fetch import Fetcher

BASE = "https://www.chess.com/service/courses/rpc"
_AUTH_SVC = "chesscom.courses.v1.CoursesAuthenticationService"
_TRAIN_SVC = "chesscom.courses.v1.CoursesTrainingService"


class CoursesClient:
    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher
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
            {"variationId": variation_id, "learningSessionId": self.session_id},
        )

    async def _call(
        self, service: str, method: str, body: dict[str, Any], *, authed: bool = True
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
        return await self._fetcher.post_json(
            f"{BASE}/{service}/{method}", headers=headers, json=body
        )
