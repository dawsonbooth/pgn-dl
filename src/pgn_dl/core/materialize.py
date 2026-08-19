"""Materialize Typer commands and sub-apps from source declarations.

Every declared extractor gets the same command body - parse the target, build
Options from the shared flags, execute - implemented once here and reused for
the `url` command too (ADR-0002). Defaults are read off `runner.Options` when
a command is built, not at import time, so a rebuild always reflects the
runner's current values.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, cast

import typer

from .declaration import SourceDeclaration
from .driver import execute
from .extractor import Extractor
from .registry import UnknownSource, resolve_url
from .runner import Options

OutOpt = Annotated[Path, typer.Option("--out", help="Output directory")]
ConcurrencyOpt = Annotated[
    int, typer.Option("--concurrency", "-j", help="Parallel item requests")
]
DelayOpt = Annotated[
    float,
    typer.Option("--delay", help="Seconds to pause after each request (jittered)"),
]
LimitOpt = Annotated[
    int | None, typer.Option("--limit", help="Stop after N items (for smoke testing)")
]


def _construct(extractor: type[Extractor]) -> Callable[[str], Extractor]:
    def make(target: str) -> Extractor:
        # Same one-positional-arg convention extractor.py's from_url() relies
        # on, and the same ignore it carries for it.
        return extractor(target)  # ty: ignore[too-many-positional-arguments]

    return make


def _resolve_url_or_exit(target: str) -> Extractor:
    try:
        return resolve_url(target)
    except UnknownSource as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None


def build_command(
    *,
    target_name: str,
    target_help: str,
    help: str,
    resolve: Callable[[str], Extractor],
) -> Callable[..., None]:
    """Build one command: parse ``target``, build Options, execute.

    The argument's declared display name is what Typer's rich help reads the
    argument's row from, not just its metavar, so the signature is assembled
    by hand (via ``__signature__``) rather than written with a fixed parameter
    name - the one seam that has to vary per declared extractor.
    """
    defaults = Options()

    def command(**kwargs: object) -> None:
        target = cast(str, kwargs[target_name])
        execute(
            resolve(target),
            Options(
                out=cast(Path, kwargs["out"]),
                concurrency=cast(int, kwargs["concurrency"]),
                delay=cast(float, kwargs["delay"]),
                limit=cast(int | None, kwargs["limit"]),
            ),
        )

    command.__signature__ = inspect.Signature(  # ty: ignore[unresolved-attribute]
        [
            inspect.Parameter(
                target_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=Annotated[str, typer.Argument(help=target_help)],
            ),
            inspect.Parameter(
                "out",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=OutOpt,
                default=defaults.out,
            ),
            inspect.Parameter(
                "concurrency",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=ConcurrencyOpt,
                default=defaults.concurrency,
            ),
            inspect.Parameter(
                "delay",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=DelayOpt,
                default=defaults.delay,
            ),
            inspect.Parameter(
                "limit",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=LimitOpt,
                default=None,
            ),
        ]
    )
    command.__doc__ = help
    return command


def build_source_app(declaration: SourceDeclaration) -> typer.Typer:
    """Build a source's sub-app: one materialized command per declared extractor."""
    sub_app = typer.Typer(no_args_is_help=True, help=declaration.label)
    for entry in declaration.extractors:
        command = build_command(
            target_name=entry.target_name,
            target_help=entry.target_help,
            help=entry.help,
            resolve=_construct(entry.extractor),
        )
        sub_app.command(name=entry.extractor.kind)(command)
    return sub_app


def build_url_command() -> Callable[..., None]:
    """Build the `url` command from the same internals every other command uses."""
    return build_command(
        target_name="target",
        target_help="Any supported URL",
        help="Download from any supported URL.",
        resolve=_resolve_url_or_exit,
    )
