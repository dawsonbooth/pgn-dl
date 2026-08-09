"""Chess.com as a pgn-dl source."""

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
from .lessons import LessonsExtractor

register_login("chesscom", login)

app = typer.Typer(no_args_is_help=True, help="chess.com")
register_app("chesscom", app)


@app.command(name="courses")
def courses_command(
    course: Annotated[str, typer.Argument(help="Course UUID or chess.com URL")],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download a course into PGN files (requires `pgn-dl login chesscom`).

    Re-running with the same out dir resumes, skipping PGNs that already exist.
    """
    execute(
        CoursesExtractor(course),
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


@app.command(name="lessons")
def lessons_command(
    target: Annotated[
        str,
        typer.Argument(help="Lesson-course or lesson URL, or 'lesson-course/lesson'"),
    ],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download chess.com lessons (requires `pgn-dl login chesscom`).

    Give a lesson-course URL to download every lesson in it, or a single lesson
    URL. A lesson course is not a chess.com Course; those are
    `pgn-dl chesscom courses`. Re-running with the same out dir resumes.
    """
    execute(
        LessonsExtractor(target),
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


__all__ = ["CoursesExtractor", "LessonsExtractor", "app"]
