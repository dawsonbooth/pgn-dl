"""Chess.com session: a persistent Chrome profile signed in once, reused after."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from ...core.browser import browser_context
from ...core.paths import profile_dir as _source_profile_dir


def profile_dir() -> Path:
    """Directory holding the persistent Chrome profile (cookies, storage)."""
    return _source_profile_dir("chesscom")


async def login() -> None:
    """Open a visible browser so the user can sign in to chess.com once."""
    async with browser_context(profile_dir(), headless=False) as ctx:
        page = await ctx.new_page()
        await page.goto("https://www.chess.com/login")
        typer.echo(
            "A browser window is open. Sign in to chess.com, then press Enter "
            "in this terminal to save the session and exit."
        )
        await asyncio.to_thread(input)
