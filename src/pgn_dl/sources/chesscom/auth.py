"""Chess.com session: a persistent Chrome profile signed in once, reused after.

chess.com has no credential file (see CONTEXT.md): its session lives in the
Chrome profile itself, so it is not a :class:`~...core.credentials.CredentialStore`
adapter. Its login still rides the credential store's shared sign-in loop -
detecting the signed-in session and exiting automatically like every other
source - but saves nothing of its own on success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from ...core.credentials import sign_in_loop
from ...core.paths import profile_dir as _source_profile_dir

#: The cookie the courses client exchanges for its run-scoped JWT (see
#: ``chesscom/client.py``'s ``AuthenticateForCoursesWithCookie`` call); its
#: presence is what a signed-in chess.com session looks like from here.
_SESSION_COOKIE = "ACCESS_TOKEN"


def profile_dir() -> Path:
    """Directory holding the persistent Chrome profile (cookies, storage)."""
    return _source_profile_dir("chesscom")


async def _probe(ctx: Any, page: Any) -> bool | None:
    for cookie in await ctx.cookies("https://www.chess.com"):
        if cookie.get("name") == _SESSION_COOKIE and cookie.get("value"):
            return True
    return None


def _on_success(_: bool) -> None:
    typer.echo("Signed in. The chess.com session is saved in the Chrome profile.")


async def login() -> None:
    """Open a visible browser so the user can sign in to chess.com once."""
    await sign_in_loop(
        "chesscom",
        login_url="https://www.chess.com/login",
        probe=_probe,
        on_success=_on_success,
        display_name="chess.com",
    )
