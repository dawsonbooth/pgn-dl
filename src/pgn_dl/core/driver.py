"""Wire an extractor to the right transport, run it, and report."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

import typer

from .browser import WindowedFetcher, browser_fetcher, page_window
from .extractor import Extractor
from .fetch import Fetcher, FetchError, httpx_fetcher
from .runner import Options, Report, items, report_discovery_failure, run

#: Said when the window has opened, which is at the first page read.
WINDOW_OPENS = (
    "A Chrome window is open to read the site's pages. "
    "Leave it open until the download ends; closing it stops the download."
)


def _profile(extractor: Extractor) -> Path:
    # The extractor names its own profile; a session belongs to one source.
    profile = extractor.browser_profile()
    if profile is None:
        raise RuntimeError(
            f"{type(extractor).__name__} needs a browser but names no profile "
            "directory; it must override browser_profile()."
        )
    return profile


async def _run(extractor: Extractor, opts: Options) -> Report:
    if extractor.needs_browser and extractor.reads_pages:
        raise RuntimeError(
            f"{type(extractor).__name__} sets both needs_browser and reads_pages; "
            "they would open the same browser profile twice."
        )
    async with AsyncExitStack() as stack:
        fetcher: Fetcher
        if extractor.needs_browser:
            profile = _profile(extractor)
            # Building the transport is where a refused profile directory
            # raises, and it happens outside the run() that catches FetchError,
            # so without this the condition `pgn-dl login` prints a message for
            # reaches a download as a traceback. Only the entry is guarded:
            # run() reports its own failures.
            try:
                fetcher = await stack.enter_async_context(browser_fetcher(profile))
            except FetchError as exc:
                report_discovery_failure(extractor, exc, typer.echo)
                return Report(stopped=True)
        else:
            fetcher = await stack.enter_async_context(httpx_fetcher())
        if extractor.reads_pages:
            # Nothing launches here: the window opens at the first page read,
            # inside run(), which reports a refused profile like any failure.
            window = await stack.enter_async_context(
                page_window(
                    _profile(extractor), on_open=lambda: typer.echo(WINDOW_OPENS)
                )
            )
            fetcher = WindowedFetcher(fetcher, window)
        return await run(extractor, fetcher, opts, echo=typer.echo)


def execute(extractor: Extractor, opts: Options) -> None:
    """Run an extractor to completion and translate the report to an exit code."""
    report = asyncio.run(_run(extractor, opts))

    if report.stopped and not report.total:
        # Nothing was listed, so discovery failed and run() has already said why.
        raise typer.Exit(code=2)

    summary = f"Done. Wrote {report.written}, skipped {report.skipped} already-present"
    if report.failed:
        summary += f", failed {report.failed}"
    summary += f" (of {items(report.total)})."
    typer.echo(summary)

    if report.stopped:
        typer.echo("Stopped early. Re-run to resume.")
        raise typer.Exit(code=2)
    if report.failed:
        typer.echo(f"{items(report.failed)} failed; re-run the same command to retry.")
        if report.failed == report.total:
            # Nothing at all arrived, and a caller reading only the exit code
            # would otherwise take that for a finished download.
            raise typer.Exit(code=1)
