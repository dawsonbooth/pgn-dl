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
from .core.fetch import FetchError
from .core.materialize import build_source_app, build_url_command
from .core.registry import declarations, iter_extractors

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
    """Materialize and mount every declared source's sub-app under its own name.

    Sources announce themselves on import, so this file names none of them.
    Sorted, so ``--help`` lists them the same way however the imports happened
    to run.
    """
    for name, declaration in sorted(declarations().items()):
        root.add_typer(build_source_app(declaration), name=name)


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
    declaration = declarations().get(source)
    fn = declaration.login if declaration is not None else None
    if fn is None:
        with_login = [n for n, d in declarations().items() if d.login is not None]
        known = ", ".join(sorted(with_login)) or "none"
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


app.command(name="url")(build_url_command())


if __name__ == "__main__":
    app()
