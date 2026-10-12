"""Download loop: extractor to a directory tree of PGN files.

Designed to survive bot-challenge interruptions:

- Each item is written to disk as soon as it's parsed, so a crash loses at most
  the in-flight requests.
- A second run skips items whose PGN file already exists, so it resumes.
- Challenge responses trigger exponential backoff with jitter; after persistent
  failures we stop cleanly with what we have.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .extractor import Extractor
from .fetch import Blocked, Fetcher, FetchError
from .io import write_text_atomic
from .models import GameItem, ItemRef
from .paths import collection_dir, combine_collection_dir, item_path
from .pgn import game_to_pgn
from .registry import has_login

_MAX_ATTEMPTS = 5

Echo = Callable[[str], None]


@dataclass(frozen=True)
class Options:
    out: Path = Path("output")
    concurrency: int = 2
    delay: float = 0.4  # jittered pause after each request
    limit: int | None = None
    backoff: float = 4.0  # seconds; doubled per attempt


@dataclass
class Report:
    total: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: bool = False


_DEFAULT_OPTIONS = Options()  # frozen, so safe to share as a default


async def _pause(opts: Options) -> None:
    """Wait out ``opts.delay``, jittered upward so requests keep no fixed cadence."""
    if opts.delay:
        await asyncio.sleep(opts.delay + random.uniform(0, opts.delay / 2))


class _PacedFetcher:
    """The run's transport, pausing after every request it carries, refused or not.

    ``--delay`` is a pause after each request, so the transport pays it rather than
    the item: a source may send several requests for one item (a listing's pages,
    a renewed token and the call retried on it), or none.
    """

    def __init__(self, inner: Fetcher, opts: Options) -> None:
        self._inner = inner
        self._opts = opts

    async def post_json(
        self, url: str, *, headers: Mapping[str, str], json: Any
    ) -> Any:
        return await self._paced(self._inner.post_json(url, headers=headers, json=json))

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        return await self._paced(self._inner.get_text(url, headers=headers))

    async def _paced[T](self, request: Awaitable[T]) -> T:
        try:
            result = await request
        except Exception:
            # Exception, not BaseException: a cancelled run stops at once
            # rather than pausing on its way out.
            await _pause(self._opts)
            raise
        await _pause(self._opts)
        return result


async def _fetch_with_retry(
    extractor: Extractor,
    fetcher: Fetcher,
    ref: ItemRef,
    opts: Options,
    echo: Echo,
    gate: asyncio.Lock,
) -> GameItem | None:
    """Fetch one item, backing off on blocks. Returns None if we give up.

    None means blocked out and nothing else; the caller reads it as grounds to
    stop the whole run.

    ``gate`` is shared by the run's workers: one holds it while it backs off, and
    every attempt waits for it first, so a block holds back every worker, not only
    the one it met.
    """
    label = "/".join(ref.path)
    # A source that blocked a request is asked again no sooner than any other.
    backoff = max(opts.backoff, opts.delay)
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        async with gate:  # wait out another worker's backoff, if one is on
            pass
        try:
            item = await extractor.fetch(fetcher, ref)
        except Blocked:
            if attempt == _MAX_ATTEMPTS:
                echo(
                    f"  ! Blocked after {attempt} attempts on '{label}'; "
                    "stopping. Re-run later to resume."
                )
                return None
            wait = backoff + random.uniform(0, backoff / 2)
            echo(
                f"  ~ Blocked on '{label}'; sleeping {wait:.1f}s "
                f"(attempt {attempt}/{_MAX_ATTEMPTS})"
            )
            async with gate:
                await asyncio.sleep(wait)
            backoff *= 2
        else:
            if item is None:
                raise ValueError("the source produced no item")
            return item


def report_discovery_failure(extractor: Extractor, exc: FetchError, echo: Echo) -> None:
    """Explain a failed cold start. Discovery is where an expired session lands.

    Public because the driver reports a transport it could not build the same
    way: that also happens before anything is listed, and the advice is the same.

    The first calls a source makes are its cheapest and least authenticated, so
    a block or an auth failure here is nearly always the session, not the item.
    The exceptions are a plain 403, the server refusing this account (content
    behind a paid tier, or not shared with it), and a 429, a limit on how fast
    this account or connection may ask; signing in again fixes neither. A
    challenge often does clear after a fresh sign-in, even at 403, so it keeps
    the hint.
    """
    if exc.status == 429:
        kind = "Rate limited"
    elif isinstance(exc, Blocked):
        kind = "Blocked by a bot challenge"
    else:
        kind = "Failed"
    echo(f"! {kind} before any item could be listed: {exc}")
    denied = exc.status == 429 or (exc.status == 403 and not isinstance(exc, Blocked))
    source = type(extractor).source
    if has_login(source) and not denied:
        echo(f"  Your session may have expired. Run: pgn-dl login {source}")
    echo("  Nothing was downloaded. Re-run the same command to resume.")


#: How a malformed but perfectly successful response surfaces once an extractor
#: starts reading it: a field the schema no longer carries, an empty list, a
#: null where a name belongs, a body that is not JSON at all (a decode error is
#: a ValueError), an array where an object was expected.
_MALFORMED = (AttributeError, IndexError, KeyError, TypeError, ValueError)


def _report_malformed_discovery(
    extractor: Extractor, exc: Exception, echo: Echo
) -> None:
    """Explain a listing response that could not be read.

    A body that is not JSON is nearly always something between us and the source
    (a CDN error page, a captive portal, a sign-in redirect), so it gets the same
    advice a transport failure does. A body that parsed into a shape the
    extractor did not expect is the source's own format, which no re-run and no
    fresh session changes. Both name the exception.
    """
    source = type(extractor).source
    if isinstance(exc, json.JSONDecodeError):
        echo(f"! The listing from {source} could not be read as JSON: {exc}")
        echo("  An error page from a proxy or a sign-in page looks like this.")
        if has_login(source):
            echo(f"  Your session may have expired. Run: pgn-dl login {source}")
        echo("  Nothing was downloaded. Re-run the same command to resume.")
        return
    echo(f"! The listing from {source} was not in the shape pgn-dl expects.")
    echo(f"  {exc!r}")
    echo(f"  {source} has most likely changed what it sends; a re-run will not help.")
    echo("  Nothing was downloaded.")


async def run(
    extractor: Extractor,
    fetcher: Fetcher,
    opts: Options = _DEFAULT_OPTIONS,
    *,
    echo: Echo = print,
) -> Report:
    """Download every item the extractor discovers, writing PGN under ``opts.out``."""
    paced = _PacedFetcher(fetcher, opts)
    try:
        collection, refs = await extractor.discover(paced)
    except FetchError as exc:  # Blocked is a FetchError, so this covers both
        report_discovery_failure(extractor, exc, echo)
        return Report(stopped=True)
    except _MALFORMED as exc:
        _report_malformed_discovery(extractor, exc, echo)
        return Report(stopped=True)
    echo(f"{collection.name} - {len(refs)} items")
    if opts.limit is not None:
        refs = refs[: opts.limit]
        echo(f"(limiting to first {opts.limit} items)")

    root = collection_dir(opts.out, collection.source, collection.kind, collection.slug)
    report = Report(total=len(refs))

    # Two refs on one path write one file: whichever lands second finds the
    # first one's file and is counted as a resume skip, so the run reads as
    # complete and no later run retries it. Fail them all instead; which of the
    # two the file should hold is not ours to pick.
    shared = {path for path, n in Counter(r.path for r in refs).items() if n > 1}
    if shared:
        for ref in refs:
            if ref.path in shared:
                report.failed += 1
                label = "/".join(ref.path)
                echo(f"  ! Skipping {label!r}: another item shares this path")
        refs = [ref for ref in refs if ref.path not in shared]

    sem = asyncio.Semaphore(opts.concurrency)
    gate = asyncio.Lock()

    async def process(ref: ItemRef) -> None:
        if report.stopped:
            return
        path = item_path(root, ref.path)
        if path.exists():
            report.skipped += 1
            return
        label = "/".join(ref.path)
        async with sem:
            if report.stopped:
                return
            try:
                item = await _fetch_with_retry(extractor, paced, ref, opts, echo, gate)
            except Exception as exc:  # noqa: BLE001
                # An unexpected error on one item must not abort the batch;
                # leave it unwritten so a re-run retries it.
                report.failed += 1
                echo(f"  ! Skipping {label!r}: {exc!r}")
                return
            if item is None:
                report.stopped = True
                return

        try:
            text = item.pgn if item.pgn is not None else game_to_pgn(collection, item)
            if not text.strip():
                # A one-byte file counts as written and is skipped by every
                # later run.
                raise ValueError("the item produced no PGN")
            write_text_atomic(path, text.rstrip("\n") + "\n")
        except Exception as exc:  # noqa: BLE001
            report.failed += 1
            echo(f"  ! Skipping {label!r}: {exc!r}")
            return
        report.written += 1
        done = report.written + report.skipped
        echo(f"  [{done:>4}/{report.total}] {label}")

    await asyncio.gather(*(process(ref) for ref in refs))

    if root.is_dir():
        combined = combine_collection_dir(root, echo=echo)
        echo(f"Combined PGN: {combined}")

    return report
