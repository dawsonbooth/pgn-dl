"""BrowserFetcher against a fake Playwright context: no Chrome, no network.

``playwright.async_api.Error`` is imported for real, because telling it from an
exception of our own is the behavior under test. It comes from the ``browser``
extra, which the documented test command installs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from pgn_dl.core.browser import BrowserFetcher
from pgn_dl.core.fetch import Blocked, FetchError


class FakeResponse:
    def __init__(self, status: int, headers: Mapping[str, str], text: str) -> None:
        self.status = status
        self.headers = dict(headers)
        self._text = text

    async def text(self) -> str:
        return self._text


class FakeRequest:
    def __init__(self, resp: FakeResponse) -> None:
        self._resp = resp

    async def post(self, url: str, **kw: Any) -> FakeResponse:
        return self._resp

    async def get(self, url: str, **kw: Any) -> FakeResponse:
        return self._resp


class FakeContext:
    def __init__(self, resp: FakeResponse) -> None:
        self.request = FakeRequest(resp)


def _fetcher(resp: FakeResponse) -> BrowserFetcher:
    return BrowserFetcher(FakeContext(resp))


async def test_post_json_detects_a_challenge_served_with_status_200() -> None:
    # Cloudflare can mitigate a request without failing it. Gating the check on
    # `ok` would let this through, and the JSON decode would surface as a plain
    # failure instead of a block the runner backs off from.
    resp = FakeResponse(200, {"cf-mitigated": "challenge"}, "<html>Just a moment")
    with pytest.raises(Blocked):
        await _fetcher(resp).post_json("https://x/y", headers={}, json={})


async def test_get_text_detects_a_challenge_served_with_status_200() -> None:
    resp = FakeResponse(200, {"cf-mitigated": "challenge"}, "<html>Just a moment")
    with pytest.raises(Blocked):
        await _fetcher(resp).get_text("https://x/y", headers={})


async def test_a_plain_failure_is_a_fetch_error_not_a_block() -> None:
    resp = FakeResponse(500, {}, "boom")
    with pytest.raises(FetchError) as exc:
        await _fetcher(resp).get_text("https://x/y", headers={})
    assert not isinstance(exc.value, Blocked)


class BoomContext:
    """A context whose every request fails."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.request = self

    async def post(self, url: str, **kw: Any) -> FakeResponse:
        raise self._exc

    async def get(self, url: str, **kw: Any) -> FakeResponse:
        raise self._exc


async def test_a_browser_transport_failure_is_a_fetch_error() -> None:
    # chess.com is browser-only, so this is its ordinary failure path: a reset
    # connection or a context closed under us must reach the runner typed.
    exc = PlaywrightError("net::ERR_CONNECTION_RESET at https://x/y?key=sekrit")
    fetcher = BrowserFetcher(BoomContext(exc))

    with pytest.raises(FetchError) as got:
        await fetcher.get_text("https://x/y?key=sekrit", headers={"authorization": "Bearer sekrit"})
    assert "sekrit" not in str(got.value)

    with pytest.raises(FetchError):
        await fetcher.post_json("https://x/y", headers={}, json={})


async def test_a_bug_in_our_own_code_is_not_dressed_up_as_a_transport_failure() -> None:
    # Catching every exception here would tally a TypeError of ours as one
    # failed item with a "request failed" message, and the traceback that names
    # the real defect would never be printed.
    fetcher = BrowserFetcher(BoomContext(TypeError("headers must be a mapping")))

    with pytest.raises(TypeError):
        await fetcher.get_text("https://x/y", headers={})
    with pytest.raises(TypeError):
        await fetcher.post_json("https://x/y", headers={}, json={})


async def test_a_non_json_200_from_the_browser_is_a_fetch_error() -> None:
    resp = FakeResponse(200, {}, "<html>502 Bad Gateway</html>")
    with pytest.raises(FetchError):
        await _fetcher(resp).post_json("https://x/y", headers={}, json={})


async def test_ordinary_responses_pass_through() -> None:
    ok = FakeResponse(200, {}, '{"a": 1}')
    assert await _fetcher(ok).post_json("https://x/y", headers={}, json={}) == {"a": 1}
    assert await _fetcher(FakeResponse(200, {}, "hi")).get_text(
        "https://x/y", headers={}
    ) == "hi"
