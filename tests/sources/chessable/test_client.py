"""GameClient against a fake fetcher: no network, no credentials on the wire."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessable.client import GameClient


class FakeFetcher:
    """Records the last GET and answers with a canned body."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.url: str | None = None
        self.headers: Mapping[str, str] = {}

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.url = url
        self.headers = headers
        return self.body

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        raise AssertionError("chessable only issues GETs")


def _client(body: str) -> tuple[GameClient, FakeFetcher]:
    fetcher = FakeFetcher(body)
    return GameClient(fetcher, jwt="DUMMY-JWT", uid="424242"), fetcher


async def test_get_course_targets_the_api_with_uid_and_bid() -> None:
    client, fetcher = _client('{"course": {"data": []}}')
    data = await client.get_course("277570")
    assert data == {"course": {"data": []}}
    assert fetcher.url is not None
    assert "/api/v1/getCourse" in fetcher.url
    assert "uid=424242" in fetcher.url
    assert "bid=277570" in fetcher.url
    assert "includeVariations=true" in fetcher.url


async def test_get_game_targets_the_api_with_oid_and_course() -> None:
    client, fetcher = _client('{"game": {"data": []}}')
    await client.get_game("277570", "44985871")
    assert fetcher.url is not None
    assert "/api/v1/getGame" in fetcher.url
    assert "oid=44985871" in fetcher.url
    assert "cid=277570" in fetcher.url


async def test_every_request_carries_the_bearer_and_a_browser_user_agent() -> None:
    # Cloudflare 403s the default httpx User-Agent, and the API needs the JWT.
    client, fetcher = _client('{"course": {"data": []}}')
    await client.get_course("277570")
    assert fetcher.headers["Authorization"] == "Bearer DUMMY-JWT"
    assert "Mozilla/" in fetcher.headers["User-Agent"]


async def test_a_non_json_body_is_a_fetch_error_without_the_body() -> None:
    # A Cloudflare or sign-in HTML page must fail typed, and must not echo a body
    # that a credentialed request could have reflected.
    client, _ = _client("<html>Just a moment…</html>")
    with pytest.raises(FetchError) as exc:
        await client.get_course("277570")
    assert "Just a moment" not in str(exc.value)


async def test_credentials_never_appear_in_the_url_or_a_recorded_place() -> None:
    # The uid is on the query string (Chessable's own design), but the JWT is a
    # header only; a stray token in a URL would land in logs and referers.
    client, fetcher = _client('{"game": {"data": []}}')
    await client.get_game("277570", "1")
    assert "DUMMY-JWT" not in (fetcher.url or "")


async def test_json_body_that_is_not_an_object_is_refused() -> None:
    # A 200 that decodes to a list or number is not a getCourse/getGame answer.
    client, _ = _client("[1, 2, 3]")
    with pytest.raises(FetchError):
        await client.get_course("1")
