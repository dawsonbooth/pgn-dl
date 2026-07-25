from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from . import auth, scraper

app = typer.Typer(no_args_is_help=True, add_completion=False)

_DEFAULT_OUT = Path("output")


@app.command()
def login() -> None:
    """Open a visible browser so you can sign in to chess.com once.

    The session cookies persist in `.profile/` for subsequent `scrape` runs.
    """
    asyncio.run(_login())


async def _login() -> None:
    async with auth.browser_context(headless=False) as ctx:
        page = await ctx.new_page()
        await page.goto("https://www.chess.com/login")
        typer.echo(
            "A browser window is open. Sign in to chess.com, then press Enter "
            "in this terminal to save the session and exit."
        )
        await asyncio.to_thread(input)


@app.command()
def scrape(
    course: Annotated[str, typer.Argument(help="Course UUID or chess.com URL")],
    out: Annotated[Path, typer.Option("--out", help="Output directory")] = _DEFAULT_OUT,
    concurrency: Annotated[
        int, typer.Option("--concurrency", "-j", help="Parallel lesson requests")
    ] = 2,
    delay: Annotated[
        float,
        typer.Option("--delay", help="Seconds to pause after each request (jittered)"),
    ] = 0.4,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Stop after N lessons (for smoke testing)"),
    ] = None,
) -> None:
    """Scrape a course into PGN files (requires prior login).

    Re-running with the same out dir resumes, skipping PGNs that already exist.
    """
    course_id = auth.course_id_from_url(course)
    asyncio.run(
        scraper.scrape_course(
            course_id, out, concurrency=concurrency, delay=delay, limit=limit
        )
    )


if __name__ == "__main__":
    app()
