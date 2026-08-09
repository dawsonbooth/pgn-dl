"""The shared CLI options and the defaults they advertise."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from pgn_dl.core import cliopts, runner
from pgn_dl.core.runner import Options


def test_the_cli_defaults_are_the_runners_own() -> None:
    # Every source's `--help` prints these, and every download uses Options'
    # field defaults. A second copy in cliopts would drift silently.
    defaults = Options()
    assert cliopts.DEFAULT_OUT == defaults.out
    assert cliopts.DEFAULT_CONCURRENCY == defaults.concurrency
    assert cliopts.DEFAULT_DELAY == defaults.delay


class _DriftedOptions:
    """Stands in for the runner's Options, sharing none of its values."""

    out = Path("somewhere-else")
    concurrency = 11
    delay = 2.5


def test_the_cli_defaults_are_read_from_the_runner_rather_than_copied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Equality alone would pass against a hardcoded second copy: the copy was
    # equal the day it was written, and drift is the whole thing being guarded
    # against. Giving the runner different defaults and re-importing is what
    # tells "derived from" apart from "equal by coincidence".
    monkeypatch.setattr(runner, "Options", _DriftedOptions)
    try:
        drifted = importlib.reload(cliopts)
        assert drifted.DEFAULT_OUT == _DriftedOptions.out
        assert drifted.DEFAULT_CONCURRENCY == _DriftedOptions.concurrency
        assert drifted.DEFAULT_DELAY == _DriftedOptions.delay
    finally:
        # Undone before the reload, so the module is left holding the real
        # values whatever happened above.
        monkeypatch.undo()
        importlib.reload(cliopts)
