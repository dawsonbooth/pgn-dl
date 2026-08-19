"""The contract every source implements.

``discover`` returns every item's output path without fetching it, so the runner
can skip already-downloaded items with no network traffic. ``fetch`` then
produces one item.

The runner always calls ``discover`` once and ``fetch`` many times after it, on
the same instance and with the same transport (see ``core/runner.py``): the
extractor instance is the run. What discovery produces beyond the listing - a
session or client it opened, an artifact it parsed, per-item metadata a fetch
reads - is this run's *fetch context* (see CONTEXT.md). A concrete source
supplies a discovery step that builds it and a fetch step that receives it;
``discover``/``fetch`` below are the template that wires the two together and
the only place the discover-before-fetch invariant is enforced, so a source
cannot express the bug of skipping it.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import ClassVar, Self, final

from .fetch import Fetcher
from .models import Collection, GameItem, ItemRef
from .paths import profile_dir


class NotDiscovered(RuntimeError):
    """``fetch`` was called on an extractor ``discover`` never ran on.

    The runner always discovers before it fetches, so reaching this means our
    own code broke that ordering - a source, or a caller bypassing the runner.
    Per docs/open-issues.md 4.1, a bug of ours should stay debuggable, so this
    is deliberately left for the caller to leave uncaught rather than folded
    into a per-item failure the way a source's own errors are.
    """


class _Unset:
    """Sentinel for "no fetch context yet", distinct from every real one.

    A context type could plausibly be ``None`` itself (a source with nothing to
    carry between discovery and fetch), so the not-yet-discovered state needs a
    marker ``None`` cannot be confused with.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<undiscovered>"


_UNSET = _Unset()


class Once[T]:
    """A value built at most once, the first time something asks for it.

    Concurrent fetches under the runner's semaphore can all reach for the same
    lazily built artifact at once; without the lock each would build its own
    copy. Only whichever caller arrives first pays for ``build``, and the rest
    receive its result.

    A failed build is deliberately not cached: a transient failure (a bot
    challenge, say) is retried by the runner, and caching it would turn one bad
    moment into a whole run's worth of failed items.
    """

    def __init__(self) -> None:
        self._value: T | _Unset = _UNSET
        self._lock = asyncio.Lock()

    async def get(self, build: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            value = self._value
            if isinstance(value, _Unset):
                value = await build()
                self._value = value
            return value


class Extractor[C](ABC):
    key: ClassVar[str] = ""  # "<source>:<kind>", e.g. "chesscom:courses"
    url_patterns: ClassVar[Sequence[re.Pattern[str]]] = ()
    needs_browser: ClassVar[bool] = False

    # Both halves of ``key``, split out once. They name the output directory, so
    # a source must never restate them as literals.
    source: ClassVar[str] = ""
    kind: ClassVar[str] = ""

    def __init__(self) -> None:
        # Set for real by discover(); every subclass constructor must chain
        # here first so fetch() has this to check before discover() ever runs.
        self._context: C | _Unset = _UNSET

    def __init_subclass__(cls, **kw: object) -> None:
        super().__init_subclass__(**kw)
        if not cls.key:
            return  # an intermediate base, not a concrete extractor
        cls.source, _, cls.kind = cls.key.partition(":")
        from .registry import register

        register(cls)

    @classmethod
    def collection(
        cls, *, slug: str, name: str, headers: dict[str, str] | None = None
    ) -> Collection:
        """Build this extractor's :class:`Collection`, identified by its key."""
        return Collection(
            source=cls.source,
            kind=cls.kind,
            slug=slug,
            name=name,
            headers=headers or {},
        )

    @classmethod
    def from_url(cls, url: str) -> Self | None:
        """Return a configured extractor if this URL belongs to it, else None.

        A URL under this source's patterns whose target still cannot be parsed
        is declined rather than raised: declining lets the registry keep
        looking rather than ending the command.
        """
        if not any(p.search(url) for p in cls.url_patterns):
            return None
        try:
            # Every concrete source is built from one target, a URL or bare
            # id; the base cannot declare that constructor without breaking
            # subclasses built differently, hence the ignore.
            return cls(url)  # ty: ignore[too-many-positional-arguments]
        except ValueError:
            return None

    def browser_profile(self) -> Path | None:
        """Persistent browser profile this source's session lives in.

        Only meaningful when ``needs_browser`` is set. Every source's session
        lives in the same place, so a source overrides this only to live
        somewhere else.
        """
        if self.needs_browser and self.source:
            return profile_dir(self.source)
        return None

    @final
    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        """Return the collection metadata and every item to download, in order."""
        collection, refs, context = await self._discover(fetcher)
        self._context = context
        return collection, refs

    @final
    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        """Fetch and parse one item."""
        context = self._context
        if isinstance(context, _Unset):
            raise NotDiscovered(
                f"{type(self).__name__}.fetch() called before discover()"
            )
        return await self._fetch(context, fetcher, ref)

    @abstractmethod
    async def _discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef], C]:
        """Return the collection, every item to download, and this run's fetch context."""

    @abstractmethod
    async def _fetch(self, context: C, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        """Fetch and parse one item, given the context ``_discover`` built."""
