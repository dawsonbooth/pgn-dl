"""chessreps.com as a pgn-dl source.

chessreps is a SvelteKit app backed by Supabase. Sign-in is interactive and
happens once in a browser; every download after that is one plain-HTTPS read
of the site's own data route, authenticated by the stored session cookie.
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
from .openings import OpeningsExtractor

register_login("chessreps", login)

app = typer.Typer(no_args_is_help=True, help="chessreps.com")
register_app("chessreps", app)


@app.command(name="openings")
def openings_command(
    opening: Annotated[
        str, typer.Argument(help="Opening URL or chessreps course slug")
    ],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download an opening course into PGN files (requires `pgn-dl login chessreps`).

    The whole repertoire is written as one file, mainline first and every
    other line as a variation, with the author's commentary and the site's
    engine evals as move comments. Each line is written again as a file of its
    own under lines/. Re-running with the same out dir resumes.
    """
    execute(
        OpeningsExtractor(opening),
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


__all__ = ["OpeningsExtractor", "app"]
