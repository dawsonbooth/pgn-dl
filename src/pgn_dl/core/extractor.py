"""The contract every source implements.

``discover`` returns every item's output path without fetching it, so the runner
can skip already-downloaded items with no network traffic. ``fetch`` then
produces one item.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Self

from .fetch import Fetcher
from .models import Collection, GameItem, ItemRef
from .paths import profile_dir


class Extractor(ABC):
    key: ClassVar[str] = ""  # "<source>:<kind>", e.g. "chesscom:courses"
    url_patterns: ClassVar[Sequence[re.Pattern[str]]] = ()
    needs_browser: ClassVar[bool] = False

    # Both halves of ``key``, split out once. They name the output directory, so
    # a source must never restate them as literals.
    source: ClassVar[str] = ""
    kind: ClassVar[str] = ""

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

    @abstractmethod
    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        """Return the collection metadata and every item to download, in order."""

    @abstractmethod
    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        """Fetch and parse one item."""
