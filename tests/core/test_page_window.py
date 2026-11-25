"""PageWindow and WindowedFetcher against a fake Chrome: no browser, no network.

``playwright.async_api.Error`` is imported for real, as in ``test_browser.py``:
telling Playwright's failures from our own bugs is behavior under test.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from pgn_dl.core import browser
from pgn_dl.core.browser import PageWindow, WindowedFetcher, page_window
from pgn_dl.core.fetch import Blocked, Fatal, FetchError, PageFetcher

PAGE = "https://site.test/opening/Some_Opening"
PARKING = "https://site.test/robots.txt"
CHALLENGE = (403, {"cf-mitigated": "challenge"}, "<title>Just a moment...</title>")

#: What the tab's fetch answers: status, headers, body; or an exception.
Answer = tuple[int, Mapping[str, str], str] | Exception


class FakeResponse:
    def __init__(self, status: int, headers: Mapping[str, str], text: str) -> None:
        self.status = status
        self.headers = dict(headers)
        self._text = text

    async def text(self) -> str:
        return self._text


class FakeTab:
    """A tab: ``goto`` parks it, ``evaluate`` runs the read script on a URL."""

    def __init__(self, chrome: FakeChrome) -> None:
        self._chrome = chrome
        self.closed = False

    async def goto(self, url: str, **kw: Any) -> FakeResponse | None:
        self._chrome.visited.append(url)
        if url == "about:blank":
            return None
        return FakeResponse(*self._chrome.parking.pop(0))

    async def evaluate(self, script: str, arg: list[Any]) -> list[Any]:
        url, wait = arg
        self._chrome.read.append(url)
        self._chrome.waits.append(wait)
        answer = self._chrome.answers[url]
        if isinstance(answer, Exception):
            raise answer
        status, headers, body = answer
        return [status, dict(headers), body]

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class FakeChrome:
    """The browser a launch yields, recording what happens to it."""

    def __init__(self, answers: Mapping[str, Answer]) -> None:
        self.answers = dict(answers)
        #: One response per parking, in order; the default parks fine forever.
        self.parking: list[tuple[int, Mapping[str, str], str]] = [(200, {}, "")] * 10
        self.launched: list[Path] = []
        self.exited = 0
        self.tabs: list[FakeTab] = []
        self.visited: list[str] = []
        self.read: list[str] = []
        self.waits: list[int] = []
        self._on_close: list[Callable[[Any], None]] = []

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        assert event == "close"
        self._on_close.append(handler)

    def quit(self) -> None:
        """Someone quits this Chrome."""
        for handler in self._on_close:
            handler(self)

    async def new_page(self) -> FakeTab:
        tab = FakeTab(self)
        self.tabs.append(tab)
        return tab

    def launch(self, profile: Path) -> Any:
        @asynccontextmanager
        async def context() -> AsyncGenerator[FakeChrome]:
            self.launched.append(profile)
            try:
                yield self
            finally:
                self.exited += 1

        return context()


def window(chrome: FakeChrome, tmp_path: Path, **kw: Any) -> PageWindow:
    return PageWindow(tmp_path / "profile", launch=chrome.launch, **kw)


async def test_chrome_opens_only_when_a_page_is_read(tmp_path: Path) -> None:
    chrome = FakeChrome({})
    await window(chrome, tmp_path).aclose()
    assert chrome.launched == []


async def test_a_page_is_read_from_a_tab_parked_on_its_site(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "<html>the page</html>")})
    win = window(chrome, tmp_path)

    assert await win.get_page(PAGE) == "<html>the page</html>"
    assert chrome.launched == [tmp_path / "profile"]
    assert chrome.visited == [PARKING]
    assert chrome.read == [PAGE]


async def test_the_tab_parks_once_per_site(tmp_path: Path) -> None:
    other = "https://elsewhere.test/a"
    chrome = FakeChrome(
        {PAGE: (200, {}, "a"), PAGE + "2": (200, {}, "b"), other: (200, {}, "c")}
    )
    win = window(chrome, tmp_path)

    await win.get_page(PAGE)
    await win.get_page(PAGE + "2")
    await win.get_page(other)

    assert chrome.visited == [PARKING, "https://elsewhere.test/robots.txt"]
    assert len(chrome.launched) == 1


async def test_the_window_is_a_visible_chrome_marked_as_automated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Headless Chrome names itself HeadlessChrome and is refused; the window is
    # served because it is a browser someone could be looking at. It keeps the
    # marks of automation: nothing here disguises it.
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    seen: list[tuple[Path, bool, bool]] = []

    def fake_context(profile: Path, *, headless: bool, marked: bool = False) -> Any:
        seen.append((profile, headless, marked))
        return chrome.launch(profile)

    monkeypatch.setattr(browser, "browser_context", fake_context)

    await PageWindow(tmp_path / "profile").get_page(PAGE)

    assert seen == [(tmp_path / "profile", False, True)]


async def test_a_read_is_given_up_after_a_minute(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    await window(chrome, tmp_path).get_page(PAGE)
    assert chrome.waits == [60_000]


async def test_opening_the_window_is_announced_once(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    said: list[str] = []
    win = window(chrome, tmp_path, on_open=lambda: said.append("open"))

    await win.get_page(PAGE)
    await win.get_page(PAGE)

    assert said == ["open"]


async def test_a_challenged_page_is_blocked(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: CHALLENGE})
    with pytest.raises(Blocked):
        await window(chrome, tmp_path).get_page(PAGE)


async def test_a_challenge_served_with_status_200_is_blocked(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {"cf-mitigated": "challenge"}, "<html>")})
    with pytest.raises(Blocked):
        await window(chrome, tmp_path).get_page(PAGE)


async def test_a_challenged_parking_is_blocked_and_tried_again(tmp_path: Path) -> None:
    # Never waited out: the read fails as a block, and the runner's backoff
    # decides when to ask again. The next read parks afresh.
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    chrome.parking = [CHALLENGE, (200, {}, "")]
    win = window(chrome, tmp_path)

    with pytest.raises(Blocked):
        await win.get_page(PAGE)
    assert chrome.read == []

    assert await win.get_page(PAGE) == "ok"
    assert chrome.visited == [PARKING, "about:blank", PARKING]


async def test_a_missing_page_is_a_failure_not_a_block(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (404, {}, "<html>Not found</html>")})
    with pytest.raises(FetchError) as exc:
        await window(chrome, tmp_path).get_page(PAGE)
    assert not isinstance(exc.value, Blocked)
    assert exc.value.status == 404


async def test_chrome_failing_is_a_transport_failure(tmp_path: Path) -> None:
    chrome = FakeChrome(
        {PAGE: PlaywrightError("Target page, context or browser closed")}
    )
    with pytest.raises(FetchError, match="before any response"):
        await window(chrome, tmp_path).get_page(PAGE)


async def test_a_bug_of_ours_is_not_dressed_up_as_a_transport_failure(
    tmp_path: Path,
) -> None:
    chrome = FakeChrome({PAGE: TypeError("not a string")})
    with pytest.raises(TypeError):
        await window(chrome, tmp_path).get_page(PAGE)


async def test_a_tab_that_breaks_is_replaced_at_the_next_read(tmp_path: Path) -> None:
    # A crashed tab, or one someone took elsewhere, costs that read and a new
    # tab, not a second Chrome on the same profile, which Chrome would refuse.
    chrome = FakeChrome({PAGE: PlaywrightError("Target crashed")})
    win = window(chrome, tmp_path)

    with pytest.raises(FetchError, match="before any response"):
        await win.get_page(PAGE)
    chrome.answers[PAGE] = (200, {}, "ok")
    assert await win.get_page(PAGE) == "ok"

    assert len(chrome.launched) == 1
    assert [tab.closed for tab in chrome.tabs] == [True, False]
    assert chrome.visited == [PARKING, PARKING]


async def test_a_window_someone_closes_stops_the_run(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    win = window(chrome, tmp_path)
    await win.get_page(PAGE)

    chrome.tabs[0].closed = True
    with pytest.raises(Fatal, match="the Chrome window was closed"):
        await win.get_page(PAGE)
    assert len(chrome.tabs) == 1


async def test_chrome_quit_under_the_run_stops_it(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    win = window(chrome, tmp_path)
    await win.get_page(PAGE)

    chrome.quit()
    chrome.answers[PAGE] = PlaywrightError("Target page, context or browser closed")
    with pytest.raises(Fatal, match="the Chrome window was closed"):
        await win.get_page(PAGE)


async def test_chrome_that_will_not_start_stops_the_run_and_says_why(
    tmp_path: Path,
) -> None:
    @asynccontextmanager
    async def missing(profile: Path) -> AsyncGenerator[Any]:
        raise PlaywrightError(
            "Chromium distribution 'chrome' is not found\nRun playwright install"
        )
        yield  # pragma: no cover - unreachable, keeps this a generator

    win = PageWindow(tmp_path / "profile", launch=missing)
    with pytest.raises(Fatal) as exc:
        await win.get_page(PAGE)
    assert str(exc.value) == (
        "Chrome would not start: Chromium distribution 'chrome' is not found"
    )


async def test_closing_the_window_closes_chrome(tmp_path: Path) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    win = window(chrome, tmp_path)
    await win.get_page(PAGE)

    await win.aclose()

    assert chrome.exited == 1


async def test_page_window_closes_what_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "ok")})
    monkeypatch.setattr(
        browser,
        "browser_context",
        lambda profile, *, headless, marked=False: chrome.launch(profile),
    )

    async with page_window(tmp_path / "profile") as win:
        await win.get_page(PAGE)

    assert chrome.exited == 1


class Inner:
    """The plain transport underneath, recording what reaches it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        self.calls.append(("post_json", url))
        return {"ok": True}

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.calls.append(("get_text", url))
        return "text"

    async def post_text(
        self, url: str, *, headers: Mapping[str, str], body: str
    ) -> str:
        self.calls.append(("post_text", url))
        return "posted"


async def test_a_windowed_fetcher_sends_only_pages_through_the_window(
    tmp_path: Path,
) -> None:
    chrome = FakeChrome({PAGE: (200, {}, "page")})
    inner = Inner()
    fetcher = WindowedFetcher(inner, window(chrome, tmp_path))

    assert await fetcher.get_text("https://api.test/x", headers={}) == "text"
    assert (
        await fetcher.post_text("https://api.test/y", headers={}, body="") == "posted"
    )
    assert await fetcher.post_json("https://api.test/z", headers={}, json={}) == {
        "ok": True
    }
    assert await fetcher.get_page(PAGE) == "page"

    assert inner.calls == [
        ("get_text", "https://api.test/x"),
        ("post_text", "https://api.test/y"),
        ("post_json", "https://api.test/z"),
    ]
    assert chrome.read == [PAGE]


def test_only_a_windowed_fetcher_is_a_page_fetcher(tmp_path: Path) -> None:
    windowed = WindowedFetcher(Inner(), window(FakeChrome({}), tmp_path))
    assert isinstance(windowed, PageFetcher)
    assert not isinstance(Inner(), PageFetcher)


async def test_a_window_that_never_opened_is_not_announced(tmp_path: Path) -> None:
    @asynccontextmanager
    async def refused(profile: Path) -> AsyncGenerator[Any]:
        raise FetchError(f"{profile} is a symbolic link; refusing to use it")
        yield  # pragma: no cover - unreachable, keeps this a generator

    said: list[str] = []
    win = PageWindow(
        tmp_path / "profile", launch=refused, on_open=lambda: said.append("open")
    )
    with pytest.raises(Fatal, match="symbolic link"):
        await win.get_page(PAGE)
    assert said == []
