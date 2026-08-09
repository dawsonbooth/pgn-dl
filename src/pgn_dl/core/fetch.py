"""Transports. An extractor talks to a :class:`Fetcher` and never to HTTP.

Bot-challenge detection lives here rather than in a source's client: whether a
response is a challenge is a property of the transport, and both fetchers need
the same answer so the runner's backoff behaves identically for either.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx


class FetchError(RuntimeError):
    """A request failed and the item cannot be produced.

    ``status`` is the HTTP status when the failure came from a response, and
    ``None`` when it did not (an expired session, an unreadable payload). Read
    it rather than the message prose: the message also contains the URL, and a
    document id that happens to contain "403" would be misread as a denial.
    """

    def __init__(self, *args: object, status: int | None = None) -> None:
        super().__init__(*args)
        self.status = status


class Blocked(FetchError):
    """A bot challenge or rate limit. Retryable: the runner backs off."""


# Cloudflare serves interstitial/challenge pages under these statuses with one
# of these markers in the body. Matching broadly (rather than only a 403 "Just a
# moment" page) lets the runner back off and resume on a challenge instead of
# treating it as a fatal error.
_CHALLENGE_STATUSES = frozenset({403, 429, 503})
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "attention required",
    "enable javascript and cookies",
)


def is_challenge(status: int, headers: Mapping[str, str], body: str) -> bool:
    if headers.get("cf-mitigated"):
        return True
    return status in _CHALLENGE_STATUSES and any(
        m in body.lower() for m in _CHALLENGE_MARKERS
    )


# A sign-in wall and a proxy's error page both arrive as an HTML failure, and
# only the first is worth signing in again for. Capped rather than searched
# whole: a body is unbounded and the giveaway is in the visible part.
_SIGN_IN = re.compile(r"sign[ -]?in|log[ -]?in|password", re.IGNORECASE)
_SIGN_IN_SCAN = 4000


def describe_body(body: str) -> str:
    """What a response body is, in words this module chose.

    The body itself never goes into an error message: the message reaches the
    terminal and may reach a log, and a transport whose requests carry a
    credential would leak it there. Nothing that does passes through here today
    - noctie's token exchange builds its own errors - and this is the rule that
    keeps it that way. What a reader needs from a failed body is not its bytes
    but whether they are looking at the source's own error or at something
    standing between them and it, which is a classification.
    """
    text = body.strip()
    if not text:
        return "empty body"
    if text.startswith("<"):
        return (
            "HTML sign-in page"
            if _SIGN_IN.search(text[:_SIGN_IN_SCAN])
            else "HTML page"
        )
    if text[0] in "{[":
        return "JSON body"
    return "text body"


def check_response(
    url: str, status: int, headers: Mapping[str, str], body: str
) -> None:
    """Raise :class:`Blocked` or :class:`FetchError` for a failed response."""
    if is_challenge(status, headers, body):
        raise Blocked(f"{url}: bot challenge (status {status})", status=status)
    if not 200 <= status < 300:
        raise FetchError(
            f"{url} failed: {status} ({describe_body(body)})", status=status
        )


class Fetcher(Protocol):
    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any: ...

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str: ...


def transport_failure(exc: Exception) -> FetchError:
    """A failure with no response at all, as something the runner can catch.

    A dropped connection, a DNS failure or a timeout never reaches
    :func:`check_response`, so unwrapped it escapes as a raw httpx or Playwright
    exception. Only the exception type goes into the message: a request carries
    credentials in its headers and sometimes in its URL, and both libraries
    render the request into some of their own messages.
    """
    return FetchError(f"request failed before any response: {type(exc).__name__}")


def not_json(url: str, status: int) -> FetchError:
    """A 2xx body that would not decode, as the same typed failure.

    An error page from a CDN, a truncated body and a 204 all pass
    :func:`check_response`. The body stays out of the message; ``post_json``'s
    callers are token exchanges, so it is a credential.
    """
    return FetchError(f"{url}: response body is not JSON", status=status)


class HttpxFetcher:
    """Plain HTTP, for sources with an open API and no bot protection."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        try:
            resp = await self._client.post(url, headers=dict(headers), json=json)
        except httpx.HTTPError as exc:
            raise transport_failure(exc) from None
        check_response(url, resp.status_code, resp.headers, resp.text)
        try:
            return resp.json()
        except ValueError:
            raise not_json(url, resp.status_code) from None

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        try:
            resp = await self._client.get(url, headers=dict(headers))
        except httpx.HTTPError as exc:
            raise transport_failure(exc) from None
        check_response(url, resp.status_code, resp.headers, resp.text)
        return resp.text


@asynccontextmanager
async def httpx_fetcher() -> AsyncIterator[HttpxFetcher]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        yield HttpxFetcher(client)
