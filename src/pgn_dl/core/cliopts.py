"""Shared CLI options, declared once so every source presents the same flags."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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

# What `--help` advertises, taken from the dataclass the runner actually uses,
# so the two cannot drift apart.
_DEFAULTS = Options()

DEFAULT_OUT = _DEFAULTS.out
DEFAULT_CONCURRENCY = _DEFAULTS.concurrency
DEFAULT_DELAY = _DEFAULTS.delay
