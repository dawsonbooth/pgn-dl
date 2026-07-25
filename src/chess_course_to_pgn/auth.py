from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

_PROFILE_ENV = "CHESS_COURSE_PROFILE"

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def profile_dir() -> Path:
    """Directory holding the persistent Chrome profile (cookies, storage).

    Defaults to ``.profile`` under the current working directory; set
    ``CHESS_COURSE_PROFILE`` to an absolute path so ``login`` and ``scrape``
    share a session even when run from different directories.
    """
    return Path(os.environ.get(_PROFILE_ENV, ".profile")).resolve()


def course_id_from_url(s: str) -> str:
    """Accept a bare UUID or any chess.com/courses URL containing one."""
    m = _UUID_RE.search(s)
    if not m:
        raise ValueError(f"could not find a course UUID in {s!r}")
    return m.group(0).lower()


@asynccontextmanager
async def browser_context(headless: bool) -> AsyncIterator[BrowserContext]:
    """Yield a Chrome context backed by the persistent profile.

    Uses the real installed Google Chrome (`channel="chrome"`) rather than
    Playwright's bundled Chromium so Cloudflare's bot checks accept the login.
    """
    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1400, "height": 900},
        )
        try:
            yield ctx
        finally:
            await ctx.close()
