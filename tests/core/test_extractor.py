"""The discover/fetch template: the fetch context reaching fetch, the
not-yet-discovered guard, and the once-per-run cache.

Prior art for the fake extractor is the runner suite's; this one exists to
prove the base's own behavior once, generically, rather than per source.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from pgn_dl.core.extractor import Extractor, NotDiscovered, Once
from pgn_dl.core.fetch import Fetcher
from pgn_dl.core.models import Collection, GameItem, ItemRef

COLLECTION = Collection(source="fake", kind="things", slug="c", name="C")
REF = ItemRef(id="1", path=("001-item",))


class FakeFetcher:
    """Satisfies the Fetcher protocol; nothing here ever calls it."""

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        raise NotImplementedError

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        raise NotImplementedError


FETCHER = FakeFetcher()


@dataclass
class _Context:
    label: str


class ContextExtractor(Extractor[_Context]):
    key = "fake:context"

    def __init__(self, label: str = "built-by-discover") -> None:
        super().__init__()
        self.label = label
        self.received: list[_Context] = []

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        return COLLECTION, [REF], _Context(label=self.label)

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        self.received.append(context)
        return GameItem()


async def test_fetch_receives_the_context_discover_built() -> None:
    extractor = ContextExtractor("built-by-discover")

    await extractor.discover(FETCHER)
    await extractor.fetch(FETCHER, REF)
    await extractor.fetch(FETCHER, REF)

    assert [c.label for c in extractor.received] == ["built-by-discover"] * 2
    # The same object every time, not a fresh copy per fetch: a source that
    # mutates its context (chess.com lessons' late-filled title, say) needs the
    # second fetch to see what the first one wrote.
    assert extractor.received[0] is extractor.received[1]


async def test_fetch_before_discover_raises_a_programming_error() -> None:
    extractor = ContextExtractor()

    with pytest.raises(NotDiscovered):
        await extractor.fetch(FETCHER, REF)


async def test_fetch_before_discover_never_reaches_the_fetch_step() -> None:
    extractor = ContextExtractor()

    with pytest.raises(NotDiscovered):
        await extractor.fetch(FETCHER, REF)

    assert extractor.received == []


async def test_once_builds_exactly_once_under_concurrent_callers() -> None:
    once: Once[int] = Once()
    builds = 0

    async def build() -> int:
        nonlocal builds
        builds += 1
        await asyncio.sleep(0)  # a real build yields; a racing caller gets in
        return 42

    results = await asyncio.gather(*(once.get(build) for _ in range(5)))

    assert results == [42] * 5
    assert builds == 1


async def test_once_does_not_cache_a_failed_build() -> None:
    once: Once[int] = Once()
    attempts = 0

    async def flaky() -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        return 7

    with pytest.raises(RuntimeError):
        await once.get(flaky)

    assert await once.get(flaky) == 7
    assert attempts == 2
