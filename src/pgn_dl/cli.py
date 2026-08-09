"""The pgn-dl command line.

Source subcommands (``pgn-dl chesscom courses ...``) are the real interface;
``pgn-dl url <target>`` dispatches by URL, and a bare ``pgn-dl <url>`` is sugar
over that.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

import click
import typer
from typer.core import TyperGroup

from . import sources  # noqa: F401  - importing registers the built-in sources
from .core.cliopts import (
    DEFAULT_CONCURRENCY,
    DEFAULT_DELAY,
    DEFAULT_OUT,
    ConcurrencyOpt,
    DelayOpt,
    LimitOpt,
    OutOpt,
)
from .core.driver import execute
from .core.fetch import FetchError
from .core.registry import UnknownSource, apps, iter_extractors, logins, resolve_url
from .core.runner import Options

_URL = re.compile(r"^https?://")


class RootGroup(TyperGroup):
    """Root group that reads a bare URL as ``url <target>``.

    Click resolves the first token as a subcommand name before any callback
    runs, so the sugar has to live here. Only URL-shaped tokens are rerouted; a
    mistyped command still gets Click's own "no such command" error.
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and _URL.match(args[0]) and (cmd := self.get_command(ctx, "url")):
            return "url", cmd, args
        return super().resolve_command(ctx, args)


def mount_sources(root: typer.Typer) -> None:
    """Mount every registered source's sub-app under its own name.

    Sources announce themselves on import, so this file names none of them.
    Sorted, so ``--help`` lists them the same way however the imports happened
    to run.
    """
    for name, sub_app in sorted(apps().items()):
        root.add_typer(sub_app, name=name)


# pretty_exceptions_show_locals is Typer's own default, pinned here: a frame
# between the token exchange and the file it lands in holds a refresh token,
# and a rendered traceback would print it.
app = typer.Typer(
    cls=RootGroup, add_completion=False, pretty_exceptions_show_locals=False
)
mount_sources(app)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Download PGN from chess.com and other sources."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


# Named explicitly because the function name would collide with the imported
# `sources` package.
@app.command(name="sources")
def list_sources() -> None:
    """List every source this build knows how to download from."""
    for cls in iter_extractors():
        patterns = ", ".join(p.pattern for p in cls.url_patterns) or "-"
        typer.echo(f"{cls.key:<24} {patterns}")


@app.command()
def login(
    source: Annotated[str, typer.Argument(help="Source name, e.g. chesscom")],
) -> None:
    """Sign in to a source once; the session persists for later downloads."""
    fn = logins().get(source)
    if fn is None:
        known = ", ".join(sorted(logins())) or "none"
        typer.echo(
            f"{source!r} needs no login, or is unknown. Sources with login: {known}"
        )
        raise typer.Exit(code=1)
    try:
        asyncio.run(fn())
    except FetchError as exc:
        # Sign-in is interactive: it gets abandoned, and it times out. Neither
        # is a defect worth a traceback.
        typer.echo(f"Login failed: {exc}")
        raise typer.Exit(code=1) from None


@app.command(name="url")
def url_command(
    target: Annotated[str, typer.Argument(help="Any supported URL")],
    out: OutOpt = DEFAULT_OUT,
    concurrency: ConcurrencyOpt = DEFAULT_CONCURRENCY,
    delay: DelayOpt = DEFAULT_DELAY,
    limit: LimitOpt = None,
) -> None:
    """Download from any supported URL."""
    try:
        extractor = resolve_url(target)
    except UnknownSource as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None
    execute(
        extractor,
        Options(out=out, concurrency=concurrency, delay=delay, limit=limit),
    )


if __name__ == "__main__":
    app()
