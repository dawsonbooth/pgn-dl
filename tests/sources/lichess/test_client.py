"""The client: named requests, one at a time, the token only where asked for,
and a 429 waited out once as Lichess asks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from pgn_dl.core.fetch import Blocked
from pgn_dl.sources.lichess import client as client_module
from pgn_dl.sources.lichess.client import RATE_LIMIT_WAIT, Client

from .conftest import (
    CHALLENGE_PAGE,
    ROOT,
    TOKEN,
    USER_AGENT,
    FakeFetcher,
    Response,
    failure,
    fake_waits,
)

URL = f"{ROOT}/api/study/aBcD1234.pgn"
OTHER = f"{ROOT}/api/study/EfGh5678.pgn"


async def test_every_request_names_pgn_dl_in_its_user_agent() -> None:
    fetcher = FakeFetcher({URL: "{}"})
    client = Client(fetcher, token=TOKEN)
    await client.text(URL)
    await client.json(URL, auth=True)
    await client.post_text(URL, "a,b")
    assert len(fetcher.requests) == 3
    assert all(h["User-Agent"] == USER_AGENT for _, _, h, _ in fetcher.requests)


@pytest.mark.parametrize(
    ("token", "auth", "sent"),
    [(TOKEN, True, True), (TOKEN, False, False), (None, True, False)],
)
async def test_the_token_goes_only_with_a_request_that_asks_for_it(
    token: str | None, auth: bool, sent: bool
) -> None:
    fetcher = FakeFetcher({URL: "{}"})
    client = Client(fetcher, token=token)
    await client.text(URL, auth=auth)
    await client.json(URL, auth=auth)
    await client.post_text(URL, "a,b", auth=auth)
    expected = f"Bearer {TOKEN}" if sent else None
    assert [h.get("Authorization") for _, _, h, _ in fetcher.requests] == [expected] * 3


async def test_a_request_sends_no_token_unless_it_asks() -> None:
    fetcher = FakeFetcher({URL: "{}"})
    client = Client(fetcher, token=TOKEN)
    await client.text(URL)
    await client.json(URL)
    await client.post_text(URL, "a,b")
    assert not any("Authorization" in h for _, _, h, _ in fetcher.requests)


def test_signed_in_says_whether_there_is_a_token() -> None:
    assert Client(FakeFetcher({}), token=TOKEN).signed_in is True
    assert Client(FakeFetcher({})).signed_in is False


async def test_a_request_names_what_it_accepts_only_when_told() -> None:
    fetcher = FakeFetcher({URL: "{}"})
    client = Client(fetcher)
    await client.text(URL)
    await client.text(URL, accept="application/x-chess-pgn")
    await client.json(URL)
    assert [h.get("Accept") for _, _, h, _ in fetcher.requests] == [
        None,
        "application/x-chess-pgn",
        "application/json",
    ]


async def test_json_decodes_the_body() -> None:
    fetcher = FakeFetcher({URL: '{"js": {"learn": {"hash": "ABCD1234"}}}'})
    assert await Client(fetcher).json(URL) == {"js": {"learn": {"hash": "ABCD1234"}}}


async def test_a_body_that_is_not_json_is_a_value_error() -> None:
    fetcher = FakeFetcher({URL: "<!DOCTYPE html><html></html>"})
    with pytest.raises(ValueError):
        await Client(fetcher).json(URL)


async def test_a_post_sends_its_body_as_plain_text() -> None:
    fetcher = FakeFetcher({URL: "exported"})
    client = Client(fetcher, token=TOKEN)
    answer = await client.post_text(URL, "id1,id2", accept="text/plain", auth=True)
    assert answer == "exported"
    [(method, url, headers, body)] = fetcher.requests
    assert (method, url, body) == ("POST", URL, "id1,id2")
    assert headers == {
        "User-Agent": USER_AGENT,
        "Accept": "text/plain",
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "text/plain",
    }


def test_the_wait_after_a_429_is_the_minute_lichess_asks_for() -> None:
    assert RATE_LIMIT_WAIT == 60.0


async def test_a_429_is_waited_out_for_a_minute_and_asked_once_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = FakeFetcher({URL: failure(URL, 429)})

    def lifted() -> None:
        fetcher.responses[URL] = "after the wait"

    asked = fake_waits(monkeypatch, lifted)
    assert await Client(fetcher, token=TOKEN).text(URL, auth=True) == "after the wait"
    assert asked == [60.0]
    first, again = fetcher.requests
    assert first == again  # the same request, token and all


async def test_a_429_on_a_post_asks_again_with_the_same_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = FakeFetcher({URL: failure(URL, 429)})

    def lifted() -> None:
        fetcher.responses[URL] = "exported"

    fake_waits(monkeypatch, lifted)
    assert await Client(fetcher).post_text(URL, "id1,id2") == "exported"
    first, again = fetcher.requests
    assert first == again
    assert first[3] == "id1,id2"


async def test_a_second_429_in_a_row_reaches_the_runner_as_a_block(
    waits: list[float],
) -> None:
    fetcher = FakeFetcher({URL: failure(URL, 429)})
    with pytest.raises(Blocked) as exc:
        await Client(fetcher).text(URL)
    assert exc.value.status == 429
    assert waits == [60.0]
    assert fetcher.urls == [URL, URL]


async def test_after_a_second_429_the_next_request_still_waits_out_the_minute(
    waits: list[float],
) -> None:
    # Lichess asks for a minute with no requests, whichever item asks next.
    fetcher = FakeFetcher({URL: failure(URL, 429), PAGE: "<html>the page</html>"})
    client = Client(fetcher)
    with pytest.raises(Blocked):
        await client.text(URL)
    assert await client.page(PAGE) == "<html>the page</html>"
    assert waits == [60.0, 60.0]


async def test_a_bot_challenge_served_as_a_429_is_not_waited_out(
    waits: list[float],
) -> None:
    challenge = failure(URL, 429, CHALLENGE_PAGE)
    fetcher = FakeFetcher({URL: challenge})
    with pytest.raises(Blocked) as exc:
        await Client(fetcher).text(URL)
    assert exc.value is challenge
    assert waits == []
    assert fetcher.urls == [URL]


@pytest.mark.parametrize("status", [403, 503])
async def test_a_bot_challenge_is_not_waited_out(
    waits: list[float], status: int
) -> None:
    challenge = failure(URL, status, CHALLENGE_PAGE)
    assert isinstance(challenge, Blocked)
    fetcher = FakeFetcher({URL: challenge})
    with pytest.raises(Blocked) as exc:
        await Client(fetcher).text(URL)
    assert exc.value is challenge
    assert waits == []
    assert fetcher.urls == [URL]


async def test_any_other_failure_is_not_asked_again(waits: list[float]) -> None:
    refused = failure(URL, 404)
    fetcher = FakeFetcher({URL: refused})
    with pytest.raises(type(refused)) as exc:
        await Client(fetcher).text(URL)
    assert exc.value is refused
    assert waits == []
    assert fetcher.urls == [URL]


class HeldFetcher(FakeFetcher):
    """Keeps every request open until ``release`` is set."""

    def __init__(self, responses: Mapping[str, Response]) -> None:
        super().__init__(responses)
        self.release = asyncio.Event()

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        answer = await super().get_text(url, headers=headers)
        await self.release.wait()
        return answer


async def test_requests_go_out_one_at_a_time() -> None:
    fetcher = HeldFetcher({URL: "one", OTHER: "two"})
    client = Client(fetcher)
    both = asyncio.gather(client.text(URL), client.text(OTHER))
    for _ in range(5):
        await asyncio.sleep(0)
    # The first is still open, so the second has not gone out.
    assert fetcher.urls == [URL]
    fetcher.release.set()
    assert await both == ["one", "two"]
    assert fetcher.urls == [URL, OTHER]


async def test_nothing_else_goes_out_while_a_429_is_waited_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_sleep = asyncio.sleep
    fetcher = FakeFetcher({URL: failure(URL, 429), OTHER: "two"})

    async def wait(seconds: float) -> None:
        for _ in range(5):
            await real_sleep(0)  # every other request gets its chance to go out
        fetcher.responses[URL] = "one"

    monkeypatch.setattr(client_module.asyncio, "sleep", wait)
    client = Client(fetcher)
    assert await asyncio.gather(client.text(URL), client.text(OTHER)) == [
        "one",
        "two",
    ]
    assert fetcher.urls == [URL, URL, OTHER]


# Pages, through the window.

PAGE = f"{ROOT}/opening/Sicilian_Defense"


async def test_a_page_goes_through_the_window_with_nothing_of_pgn_dl_s() -> None:
    # The window's Chrome asks as itself: no token, no pgn-dl User-Agent.
    fetcher = FakeFetcher({PAGE: "<html>the page</html>"})
    client = Client(fetcher, token=TOKEN)
    assert await client.page(PAGE) == "<html>the page</html>"
    assert fetcher.requests == [("PAGE", PAGE, {}, None)]


async def test_a_429_on_a_page_is_waited_out_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = FakeFetcher({PAGE: failure(PAGE, 429)})

    def lifted() -> None:
        fetcher.responses[PAGE] = "<html>the page</html>"

    asked = fake_waits(monkeypatch, lifted)
    assert await Client(fetcher).page(PAGE) == "<html>the page</html>"
    assert asked == [RATE_LIMIT_WAIT]


async def test_a_page_s_turn_is_taken_just_before_it_goes_out() -> None:
    fetcher = FakeFetcher({PAGE: "<html>the page</html>"})
    seen: list[int] = []

    async def turn() -> None:
        seen.append(len(fetcher.requests))

    await Client(fetcher).page(PAGE, turn=turn)
    assert seen == [0]
    assert fetcher.urls == [PAGE]


async def test_a_challenged_page_is_not_waited_out(waits: list[float]) -> None:
    fetcher = FakeFetcher({PAGE: failure(PAGE, 403, CHALLENGE_PAGE)})
    with pytest.raises(Blocked):
        await Client(fetcher).page(PAGE)
    assert waits == []


async def test_reading_a_page_without_a_window_is_our_own_bug() -> None:
    class NoWindow:
        async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
            raise AssertionError("not asked")

        async def post_text(
            self, url: str, *, headers: Mapping[str, str], body: str
        ) -> str:
            raise AssertionError("not asked")

        async def post_json(self, url, *, headers, json):  # pragma: no cover
            raise AssertionError("not asked")

    with pytest.raises(TypeError, match="reads_pages"):
        await Client(NoWindow()).page(PAGE)


async def test_a_page_can_ask_for_a_longer_wait_after_a_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # lila limits some pages over ten minutes, not the API's one.
    fetcher = FakeFetcher({PAGE: failure(PAGE, 429)})

    def lifted() -> None:
        fetcher.responses[PAGE] = "<html>the page</html>"

    asked = fake_waits(monkeypatch, lifted)
    assert (
        await Client(fetcher).page(PAGE, rate_limit_wait=600.0)
        == "<html>the page</html>"
    )
    assert asked == [600.0]
