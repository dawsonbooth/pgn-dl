"""Wire an extractor to the right transport, run it, and report."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import typer

from .browser import browser_fetcher
from .extractor import Extractor
from .fetch import FetchError, httpx_fetcher
from .runner import Options, Report, report_discovery_failure, run


async def _run(extractor: Extractor, opts: Options) -> Report:
    if not extractor.needs_browser:
        async with httpx_fetcher() as fetcher:
            return await run(extractor, fetcher, opts, echo=typer.echo)

    # The extractor names its own profile; a session belongs to one source.
    profile = extractor.browser_profile()
    if profile is None:
        raise RuntimeError(
            f"{type(extractor).__name__} needs a browser but names no profile "
            "directory; it must override browser_profile()."
        )
    # Building the transport is where a refused profile directory raises, and it
    # happens outside the run() that catches FetchError, so without this the
    # condition `pgn-dl login` prints a message for reaches a download as a
    # traceback. Only the entry is guarded: run() reports its own failures.
    async with AsyncExitStack() as stack:
        try:
            fetcher = await stack.enter_async_context(browser_fetcher(profile))
        except FetchError as exc:
            report_discovery_failure(extractor, exc, typer.echo)
            return Report(stopped=True)
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
    summary += f" (of {report.total} items)."
    typer.echo(summary)

    if report.stopped:
        typer.echo("Stopped early after repeated blocks. Re-run to resume.")
        raise typer.Exit(code=2)
    if report.failed:
        typer.echo(
            f"{report.failed} item(s) failed and were left unwritten; "
            "re-run the same command to retry them."
        )
        if report.failed == report.total:
            # Nothing at all arrived, and a caller reading only the exit code
            # would otherwise take that for a finished download.
            raise typer.Exit(code=1)
