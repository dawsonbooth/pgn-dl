"""chessable.com as a pgn-dl source.

Chessable is a React app over a PHP/Cloudflare backend. Sign-in is interactive
and happens once in a browser (chess.com OAuth); the JWT it leaves in
localStorage is stored, and every download after that is plain HTTPS carrying
that token, so no browser is opened for downloads.
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
from .courses import CoursesExtractor

register_login("chessable", login)

app = typer.Typer(no_args_is_help=True, help="chessable.com")
register_app("chessable", app)


@app.command(name="courses")
def courses_command(
    course: Annotated[str, typer.Argument(help="Course id or chessable.com URL")],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download a Chessable course into PGN files (requires `pgn-dl login chessable`).

    One PGN game per variation, laid out Course -> Chapter -> Variation, with the
    author's comments, evaluation glyphs, and board drawings preserved. Re-running
    with the same out dir resumes, skipping PGNs that already exist.
    """
    execute(
        CoursesExtractor(course),
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


__all__ = ["CoursesExtractor", "app", "login"]
