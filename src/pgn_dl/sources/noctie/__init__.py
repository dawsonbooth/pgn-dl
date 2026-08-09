"""noctie.ai as a pgn-dl source.

noctie is a Firebase app with no API of its own. Sign-in is interactive and
happens once in a browser; every read after that is plain HTTPS against the
Cloud Firestore REST API.
"""

from typing import Annotated

import typer

from ...core.cliopts import (
    DEFAULT_CONCURRENCY,
    DEFAULT_DELAY,
    DEFAULT_OUT,
    ConcurrencyOpt,
    DelayOpt,
    LimitOpt,
    OutOpt,
)
from ...core.driver import execute
from ...core.registry import register_app, register_login
from ...core.runner import Options
from .auth import login
from .books import OpeningBooksExtractor
from .themes import ThemesExtractor

register_login("noctie", login)

app = typer.Typer(no_args_is_help=True, help="noctie.ai")
register_app("noctie", app)


@app.command(name="themes")
def themes_command(
    theme: Annotated[str, typer.Argument(help="Theme UUID or noctie.ai theme URL")],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download a theme into PGN files (requires `pgn-dl login noctie`).

    One file per element, numbered in the theme's own order. Re-running with
    the same out dir resumes, skipping PGNs that already exist.
    """
    execute(
        ThemesExtractor(theme),
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


@app.command(name="opening-books")
def opening_books_command(
    book: Annotated[
        str, typer.Argument(help="Opening book UUID or noctie.ai opening book URL")
    ],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download an opening book and its bookmarks (requires `pgn-dl login noctie`).

    The whole repertoire is written as one file, mainline first and every
    branch as a variation. Each of the book's bookmarks is written again as a
    file of its own under bookmarks/, opening with the moves that lead to it.
    """
    execute(
        OpeningBooksExtractor(book),
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


__all__ = ["OpeningBooksExtractor", "ThemesExtractor", "app"]
