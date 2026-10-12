"""Transport behavior: challenge detection and the httpx-backed fetcher."""

from __future__ import annotations

import httpx
import pytest

from pgn_dl.core.fetch import (
    Blocked,
    FetchError,
    HttpxFetcher,
    check_response,
    is_challenge,
)


def test_cf_mitigated_header_is_a_challenge() -> None:
    assert is_challenge(200, {"cf-mitigated": "challenge"}, "")


def test_challenge_status_plus_marker_is_a_challenge() -> None:
    assert is_challenge(403, {}, "<title>Just a moment...</title>")
    assert is_challenge(429, {}, "Checking your browser before accessing")


def test_ordinary_failures_are_not_challenges() -> None:
    assert not is_challenge(403, {}, "forbidden: bad token")
    assert not is_challenge(500, {}, "just a moment")  # wrong status
    assert not is_challenge(200, {}, "")


def test_check_response_raises_blocked_for_challenges() -> None:
    with pytest.raises(Blocked):
        check_response("https://x/y", 403, {}, "Just a moment")


def test_check_response_raises_blocked_for_a_rate_limit_whatever_its_body() -> None:
    with pytest.raises(Blocked) as exc:
        check_response("https://x/y", 429, {}, '{"error": "too many requests"}')
    assert str(exc.value) == "https://x/y failed: 429 (JSON body)"
    assert exc.value.status == 429


def test_check_response_raises_fetch_error_for_other_failures() -> None:
    with pytest.raises(FetchError) as exc:
        check_response("https://x/y", 500, {}, "boom")
    assert not isinstance(exc.value, Blocked)


def test_check_response_passes_on_success() -> None:
    check_response("https://x/y", 200, {}, "{}")


def test_a_failure_carries_its_status_code() -> None:
    # Sources need to tell a 403 from a 500. Without this they read the status
    # out of the message prose, which also contains the URL: a document id
    # happening to contain "403" would then be misread as a permission denial.
    with pytest.raises(FetchError) as exc:
        check_response("https://x/y", 500, {}, "boom")
    assert exc.value.status == 500


def test_a_challenge_carries_its_status_code_too() -> None:
    with pytest.raises(Blocked) as exc:
        check_response("https://x/y", 429, {}, "Just a moment")
    assert exc.value.status == 429


def test_a_failure_names_what_the_body_was_instead_of_quoting_it() -> None:
    # No response body reaches an error message, because the runner echoes one
    # to the terminal and a log may keep it. Today no transport that carries a
    # credential passes through here; the rule is what stops the next one
    # inheriting a leak for free.
    with pytest.raises(FetchError) as exc:
        check_response("https://x/y", 500, {}, '{"refresh_token": "sekrit"}')
    assert "sekrit" not in str(exc.value)
    assert "JSON body" in str(exc.value)


def test_a_failure_distinguishes_a_sign_in_wall_from_a_broken_gateway() -> None:
    # What the excerpt was actually for: telling the source's own error from
    # something standing between the user and it. Both are HTML, and only one
    # is worth signing in again for.
    with pytest.raises(FetchError) as wall:
        check_response("https://x/y", 403, {}, "<html><h1>Sign in to continue</h1>")
    assert "sign-in" in str(wall.value)

    with pytest.raises(FetchError) as gateway:
        check_response("https://x/y", 502, {}, "<html><body>Bad Gateway</body>")
    assert "HTML page" in str(gateway.value)
    assert "sign-in" not in str(gateway.value)


def test_a_failure_with_no_body_at_all_says_so() -> None:
    with pytest.raises(FetchError) as exc:
        check_response("https://x/y", 504, {}, "")
    assert "empty body" in str(exc.value)


def test_a_fetch_error_raised_by_hand_has_no_status() -> None:
    # Sources raise FetchError for failures with no HTTP status of their own:
    # an expired session, an unreadable payload.
    assert FetchError("no saved credentials").status is None


async def test_httpx_fetcher_posts_and_returns_json() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        f = HttpxFetcher(client)
        data = await f.post_json(
            "https://x/y", headers={"authorization": "Bearer t"}, json={"a": 1}
        )

    assert data == {"ok": True}
    assert seen["url"] == "https://x/y"
    assert seen["auth"] == "Bearer t"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'"a"' in body


async def test_httpx_fetcher_maps_challenge_to_blocked() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(403, text="Just a moment...")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(Blocked):
            await HttpxFetcher(client).get_text("https://x/y", headers={})


async def test_a_body_that_is_not_json_is_a_fetch_error() -> None:
    # A CDN error page, a truncated body or a 204 all arrive as a 2xx that
    # check_response passes; decoding it outside the guard leaves a
    # JSONDecodeError for the caller.
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, text="<html>502</html>")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FetchError) as exc:
            await HttpxFetcher(client).post_json("https://x/y", headers={}, json={})
    assert exc.value.status == 200


def _dropping_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


async def test_a_dropped_connection_on_get_is_a_fetch_error() -> None:
    # A transport failure never reaches check_response, so without wrapping it
    # leaves the fetcher as a raw httpx exception: the runner catches only
    # FetchError, and a network drop during discovery becomes a traceback.
    async with httpx.AsyncClient(transport=_dropping_transport()) as client:
        with pytest.raises(FetchError) as exc:
            await HttpxFetcher(client).get_text("https://x/y", headers={})
    assert "ConnectError" in str(exc.value)
    assert exc.value.status is None


async def test_a_dropped_connection_on_post_is_a_fetch_error() -> None:
    async with httpx.AsyncClient(transport=_dropping_transport()) as client:
        with pytest.raises(FetchError) as exc:
            await HttpxFetcher(client).post_json("https://x/y", headers={}, json={})
    assert "ConnectError" in str(exc.value)


async def test_a_transport_failure_reports_no_request_detail() -> None:
    # Requests carry credentials in their headers and sometimes in the URL, so
    # the wrapper names the exception type and nothing else.
    async with httpx.AsyncClient(transport=_dropping_transport()) as client:
        with pytest.raises(FetchError) as exc:
            await HttpxFetcher(client).get_text(
                "https://x/y?key=sekrit", headers={"authorization": "Bearer sekrit"}
            )
    assert "sekrit" not in str(exc.value)
