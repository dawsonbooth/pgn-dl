"""Runner behavior against fake extractors and fetchers: no network, no browser."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from pgn_dl.core import runner as runner_module
from pgn_dl.core.extractor import Extractor
from pgn_dl.core.fetch import Blocked, FetchError
from pgn_dl.core.models import Collection, GameItem, ItemRef, MoveNode
from pgn_dl.core.runner import Options, run

COLLECTION = Collection(
    source="fake", kind="things", slug="c", name="C", headers={"Event": "C"}
)


class FakeFetcher:
    """Minimal stand-in satisfying the Fetcher protocol.

    FakeExtractor never calls these; the runner only threads the fetcher through
    to the extractor.
    """

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        raise NotImplementedError

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        raise NotImplementedError


FETCHER = FakeFetcher()


class FakeExtractor(Extractor):
    key = "fake:things"
    url_patterns = (re.compile(r"^https://fake\.test/"),)
    needs_browser = False

    def __init__(self, count: int = 3, fail_on: set[int] | None = None,
                 block_on: set[int] | None = None) -> None:
        self.count = count
        self.fail_on = fail_on or set()
        self.block_on = block_on or set()
        self.fetched: list[str] = []

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        refs = [ItemRef(id=str(i), path=(f"{i:03d}-item",)) for i in range(self.count)]
        return COLLECTION, refs

    async def fetch(self, fetcher: object, ref: ItemRef) -> GameItem:
        i = int(ref.id)
        self.fetched.append(ref.id)
        if i in self.block_on:
            raise Blocked("challenged")
        if i in self.fail_on:
            raise ValueError("bad item")
        return GameItem(root=MoveNode(san=None, children=[MoveNode(san="e4")]))


def _opts(tmp_path: Path, limit: int | None = None) -> Options:
    return Options(out=tmp_path, concurrency=2, delay=0.0, limit=limit)


async def test_writes_one_pgn_per_item_plus_combined(tmp_path: Path) -> None:
    report = await run(FakeExtractor(3), FETCHER, _opts(tmp_path), echo=lambda _: None)

    coll = tmp_path / "fake/things/c"
    assert report.written == 3
    assert report.failed == 0
    assert not report.stopped
    assert sorted(p.name for p in coll.glob("*.pgn")) == [
        "000-item.pgn", "001-item.pgn", "002-item.pgn", "all.pgn",
    ]
    assert "e4" in (coll / "000-item.pgn").read_text(encoding="utf-8")


async def test_existing_files_are_skipped_without_fetching(tmp_path: Path) -> None:
    coll = tmp_path / "fake/things/c"
    coll.mkdir(parents=True)
    (coll / "001-item.pgn").write_text('[Event "C"]\n\n*\n', encoding="utf-8")

    ex = FakeExtractor(3)
    report = await run(ex, FETCHER, _opts(tmp_path), echo=lambda _: None)

    assert report.written == 2
    assert report.skipped == 1
    assert "1" not in ex.fetched  # never requested


async def test_one_failure_does_not_abort_the_batch(tmp_path: Path) -> None:
    report = await run(
        FakeExtractor(3, fail_on={1}), FETCHER, _opts(tmp_path), echo=lambda _: None
    )

    assert report.written == 2
    assert report.failed == 1
    coll = tmp_path / "fake/things/c"
    assert not (coll / "001-item.pgn").exists()  # left unwritten so a re-run retries


async def test_persistent_block_stops_cleanly_and_keeps_what_it_has(tmp_path: Path) -> None:
    ex = FakeExtractor(3, block_on={0, 1, 2})
    report = await run(
        ex, FETCHER, Options(out=tmp_path, concurrency=1, delay=0.0, backoff=0.0),
        echo=lambda _: None,
    )

    assert report.stopped
    assert report.written == 0
    # Five attempts on the first item before giving up.
    assert ex.fetched.count("0") == 5


async def test_limit_truncates_the_plan(tmp_path: Path) -> None:
    report = await run(
        FakeExtractor(5), FETCHER, _opts(tmp_path, limit=2), echo=lambda _: None
    )
    assert report.total == 2
    assert report.written == 2


class BlockedAtDiscoveryExtractor(FakeExtractor):
    key = "fake:blocked-discovery"

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        raise Blocked(
            "https://fake.test/session: bot challenge (status 403)", status=403
        )


async def test_a_block_during_discovery_stops_cleanly(tmp_path: Path) -> None:
    lines: list[str] = []
    report = await run(
        BlockedAtDiscoveryExtractor(), FETCHER, _opts(tmp_path), echo=lines.append
    )

    # No traceback escapes: the caller gets a report, and driver.execute turns
    # `stopped` into the documented exit code 2.
    assert report.stopped
    assert report.total == report.written == 0
    assert any("re-run" in line.lower() for line in lines)


class DeniedAtDiscoveryExtractor(FakeExtractor):
    key = "fake:denied-discovery"

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        raise FetchError("https://fake.test/doc failed: 403 PERMISSION_DENIED", status=403)


class BrokenAtDiscoveryExtractor(FakeExtractor):
    key = "fake:broken-discovery"

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        raise FetchError("https://fake.test/doc failed: 500 upstream error", status=500)


def _pretend_source_has_a_login(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop() -> None: ...

    monkeypatch.setattr(runner_module, "logins", lambda: {"fake": noop})


async def test_a_denied_discovery_does_not_blame_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 403 is the server refusing this account: a paid tier, or content not
    # shared with it. Telling the user to log in again sends them down the
    # wrong path.
    _pretend_source_has_a_login(monkeypatch)
    lines: list[str] = []
    report = await run(
        DeniedAtDiscoveryExtractor(), FETCHER, _opts(tmp_path), echo=lines.append
    )

    assert report.stopped
    assert not any("login" in line for line in lines)
    assert any("Nothing was downloaded" in line for line in lines)


async def test_another_discovery_failure_still_suggests_logging_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Discovery is the cheapest, least authenticated thing a source does, so a
    # failure there is usually the session.
    _pretend_source_has_a_login(monkeypatch)
    lines: list[str] = []
    await run(BrokenAtDiscoveryExtractor(), FETCHER, _opts(tmp_path), echo=lines.append)

    assert any("login fake" in line for line in lines)


async def test_a_block_during_discovery_still_suggests_logging_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A bot challenge often does clear after a fresh sign-in, even at 403.
    _pretend_source_has_a_login(monkeypatch)
    lines: list[str] = []
    await run(
        BlockedAtDiscoveryExtractor(), FETCHER, _opts(tmp_path), echo=lines.append
    )

    assert any("login fake" in line for line in lines)


class MalformedDiscoveryExtractor(FakeExtractor):
    key = "fake:malformed-discovery"

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        raise self.exc


# Every one of these is a 200 response a CDN, a schema change or an unusual
# document can produce, and each is how one of them surfaces in an extractor.
_MALFORMED = [
    KeyError("learningSession"),
    IndexError("list index out of range"),
    TypeError("expected string or bytes-like object, got 'NoneType'"),
    AttributeError("'list' object has no attribute 'get'"),
    json.JSONDecodeError("Expecting value", "<html>Bad gateway</html>", 0),
]


@pytest.mark.parametrize("exc", _MALFORMED, ids=lambda e: type(e).__name__)
async def test_malformed_discovery_data_stops_cleanly_rather_than_raising(
    tmp_path: Path, exc: Exception
) -> None:
    # A traceback tells the user nothing to act on and reads as a broken tool
    # rather than odd upstream data.
    lines: list[str] = []
    report = await run(
        MalformedDiscoveryExtractor(exc), FETCHER, _opts(tmp_path), echo=lines.append
    )

    assert report.stopped
    assert report.total == report.written == 0
    assert any("Nothing was downloaded" in line for line in lines)


async def test_an_unreadable_body_and_a_changed_schema_are_advised_differently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A body that is not JSON is usually something between us and the source (an
    # error page, a sign-in redirect), so the session hint applies and a re-run
    # may work. A body that parsed into the wrong shape is upstream's own
    # format, which no re-run and no fresh session changes.
    _pretend_source_has_a_login(monkeypatch)
    unreadable: list[str] = []
    await run(
        MalformedDiscoveryExtractor(json.JSONDecodeError("Expecting value", "<h", 0)),
        FETCHER, _opts(tmp_path), echo=unreadable.append,
    )
    changed: list[str] = []
    await run(
        MalformedDiscoveryExtractor(KeyError("learningSession")),
        FETCHER, _opts(tmp_path), echo=changed.append,
    )

    assert any("login fake" in line for line in unreadable)
    assert any("resume" in line for line in unreadable)
    assert not any("login" in line for line in changed)
    # The exception is named, so a bug in our own reading stays diagnosable.
    assert any("learningSession" in line for line in changed)


class NoItemExtractor(FakeExtractor):
    key = "fake:no-item"

    async def fetch(self, fetcher: object, ref: ItemRef) -> GameItem:
        # A source that fell through its own guards. Only the runner's own
        # give-up path may mean "blocked".
        return cast(GameItem, None)


async def test_an_item_that_comes_back_as_nothing_is_failed_not_read_as_a_block(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    report = await run(NoItemExtractor(2), FETCHER, _opts(tmp_path), echo=lines.append)

    assert not report.stopped  # nothing was blocked
    assert report.failed == 2
    assert report.written == 0


class SharedPathExtractor(FakeExtractor):
    key = "fake:shared-path"

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        refs = [ItemRef(id=str(i), path=("001-item",)) for i in range(2)]
        return COLLECTION, refs


async def test_two_items_on_one_path_are_failed_rather_than_read_as_a_resume(
    tmp_path: Path,
) -> None:
    # Whichever lands first is the file; the other finds it already there and is
    # counted as a skip, so the run reports written=1 skipped=1 and the second
    # item is never downloaded on any later run either.
    ex = SharedPathExtractor()
    lines: list[str] = []
    report = await run(ex, FETCHER, _opts(tmp_path), echo=lines.append)

    assert report.total == 2
    assert report.failed == 2
    assert report.written == report.skipped == 0
    assert ex.fetched == []
    assert not (tmp_path / "fake/things/c/001-item.pgn").exists()
    assert any("001-item" in line for line in lines)


class EmptyPgnExtractor(FakeExtractor):
    key = "fake:empty-pgn"

    async def fetch(self, fetcher: object, ref: ItemRef) -> GameItem:
        return GameItem(pgn="")


async def test_an_empty_pgn_is_failed_rather_than_written(tmp_path: Path) -> None:
    # A one-byte file counts as written and is skipped by every later run.
    report = await run(
        EmptyPgnExtractor(1), FETCHER, _opts(tmp_path), echo=lambda _: None
    )

    assert report.written == 0
    assert report.failed == 1
    assert not (tmp_path / "fake/things/c/000-item.pgn").exists()


class InFlightExtractor(FakeExtractor):
    """Records the most items ever being fetched at the same moment."""

    key = "fake:in-flight"

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.in_flight = 0
        self.peak = 0

    async def fetch(self, fetcher: object, ref: ItemRef) -> GameItem:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        # A real fetch is a round trip, so it yields; without that every item
        # would run to completion before the next one started and the peak
        # would read as 1 whatever the semaphore allowed.
        await asyncio.sleep(0.01)
        try:
            return await super().fetch(fetcher, ref)
        finally:
            self.in_flight -= 1


@pytest.mark.parametrize("concurrency", [1, 2, 3])
async def test_no_more_items_are_in_flight_than_concurrency_allows(
    tmp_path: Path, concurrency: int
) -> None:
    # -j is one of the two anti-bot-challenge controls: an unbounded runner
    # opens twelve requests at once and gets the session challenged, which is
    # the failure the backoff exists to recover from.
    ex = InFlightExtractor(12)
    await run(
        ex,
        FETCHER,
        Options(out=tmp_path, concurrency=concurrency, delay=0.0),
        echo=lambda _: None,
    )

    assert ex.peak == concurrency
    assert len(ex.fetched) == 12


def _recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record requested sleeps instead of paying them; returns the log."""
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def record(delay: float, *args: object, **kw: object) -> None:
        slept.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(runner_module.asyncio, "sleep", record)
    return slept


async def test_the_configured_delay_is_paid_after_each_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other anti-challenge control. Sleeping for real would make this test
    # as slow as the pause it is checking, so the sleeps are recorded instead.
    slept = _recorded_sleeps(monkeypatch)

    await run(
        FakeExtractor(3),
        FETCHER,
        Options(out=tmp_path, concurrency=1, delay=0.5),
        echo=lambda _: None,
    )

    assert len(slept) == 3  # one per item, not one per run
    # Jittered upward, so a fixed cadence never emerges from the traffic.
    assert all(0.5 <= d <= 0.75 for d in slept), slept
    assert len(set(slept)) > 1


async def test_no_delay_is_paid_when_it_is_switched_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept = _recorded_sleeps(monkeypatch)

    await run(FakeExtractor(3), FETCHER, _opts(tmp_path), echo=lambda _: None)

    assert slept == []


async def test_all_pgn_is_written_at_the_collection_root(tmp_path: Path) -> None:
    await run(FakeExtractor(2), FETCHER, _opts(tmp_path), echo=lambda _: None)
    combined = (tmp_path / "fake/things/c/all.pgn").read_text(encoding="utf-8")
    assert combined.count("[Event ") == 2
