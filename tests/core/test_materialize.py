"""Commands and sub-apps materialized from a source declaration."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from pgn_dl.core import materialize
from pgn_dl.core.declaration import ExtractorDeclaration, SourceDeclaration
from pgn_dl.core.extractor import Extractor
from pgn_dl.core.materialize import build_source_app, build_url_command
from pgn_dl.core.models import Collection, GameItem, ItemRef
from pgn_dl.core.registry import UnknownSource
from pgn_dl.core.runner import Options

runner = CliRunner()


class WidgetExtractor(Extractor[None]):
    key = "widgetsource:widgets"

    def __init__(self, target: str) -> None:
        super().__init__()
        self.target = target

    async def _discover(
        self, fetcher: object
    ) -> tuple[Collection, list[ItemRef], None]:
        return self.collection(slug="s", name="S"), [], None

    async def _fetch(self, context: None, fetcher: object, ref: ItemRef) -> GameItem:
        return GameItem()


class GadgetExtractor(WidgetExtractor):
    key = "widgetsource:gadgets"


_DECLARATION = SourceDeclaration(
    label="Widget Source",
    extractors=(
        ExtractorDeclaration(
            extractor=WidgetExtractor,
            help="Download widgets into PGN files.",
            target_name="widget",
            target_help="Widget id.",
        ),
        ExtractorDeclaration(
            extractor=GadgetExtractor,
            help="Download gadgets into PGN files.",
            target_name="gadget",
            target_help="Gadget id.",
        ),
    ),
)


def test_command_names_are_taken_from_the_extractors_kind() -> None:
    app = build_source_app(_DECLARATION)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "widgets" in result.output
    assert "gadgets" in result.output


def test_help_text_and_target_display_name_come_from_the_declaration() -> None:
    app = build_source_app(_DECLARATION)
    result = runner.invoke(app, ["widgets", "--help"])
    assert result.exit_code == 0
    assert "Download widgets into PGN files." in result.output
    assert "widget" in result.output
    assert "Widget id." in result.output


def test_flags_and_defaults_match_the_runners_options() -> None:
    app = build_source_app(_DECLARATION)
    result = runner.invoke(app, ["widgets", "--help"])
    defaults = Options()
    assert "--concurrency" in result.output
    assert "--delay" in result.output
    assert "--limit" in result.output
    assert f"[default: {defaults.out}]" in result.output
    assert f"[default: {defaults.concurrency}]" in result.output


class _DriftedOptions:
    """Stands in for the runner's Options, sharing none of its values."""

    out = Path("somewhere-else")
    concurrency = 11
    delay = 2.5


def test_defaults_are_read_live_when_a_command_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Rebuilding against a drifted Options has to change the built command with
    # no module reload: the builder reads Options() at build time, not import
    # time, so a second build against a patched runner sees the new values.
    monkeypatch.setattr(materialize, "Options", _DriftedOptions)
    drifted_app = build_source_app(_DECLARATION)
    result = runner.invoke(drifted_app, ["widgets", "--help"])
    assert f"[default: {_DriftedOptions.out}]" in result.output
    assert f"[default: {_DriftedOptions.concurrency}]" in result.output

    monkeypatch.undo()
    restored_app = build_source_app(_DECLARATION)
    restored = runner.invoke(restored_app, ["widgets", "--help"])
    real = Options()
    assert f"[default: {real.out}]" in restored.output


def test_build_command_constructs_the_extractor_and_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, Options]] = []

    def _record(extractor: object, opts: Options) -> None:
        calls.append((extractor, opts))

    monkeypatch.setattr(materialize, "execute", _record)
    app = build_source_app(_DECLARATION)
    result = runner.invoke(
        app, ["widgets", "abc123", "--out", "/tmp/out", "--concurrency", "5"]
    )
    assert result.exit_code == 0, result.output
    [(extractor, opts)] = calls
    assert isinstance(extractor, WidgetExtractor)
    assert extractor.target == "abc123"
    assert opts == Options(out=Path("/tmp/out"), concurrency=5)


def test_url_command_is_built_from_the_same_internals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def _record(extractor: object, opts: Options) -> None:
        calls.append(extractor)

    monkeypatch.setattr(materialize, "execute", _record)
    monkeypatch.setattr(
        materialize, "resolve_url", lambda target: WidgetExtractor(target)
    )

    # A single-command Typer app collapses to that command directly, so no
    # leading "url" token is needed here.
    app = typer.Typer()
    app.command(name="url")(build_url_command())
    result = runner.invoke(app, ["https://example.com/w1"])

    assert result.exit_code == 0, result.output
    [extractor] = calls
    assert isinstance(extractor, WidgetExtractor)
    assert extractor.target == "https://example.com/w1"


def test_url_command_reports_unknown_sources_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _reject(target: str) -> object:
        msg = f"no source handles {target!r}; known: widgetsource:widgets"
        raise UnknownSource(msg)

    monkeypatch.setattr(materialize, "resolve_url", _reject)

    app = typer.Typer()
    app.command(name="url")(build_url_command())
    result = runner.invoke(app, ["https://example.com/nope"])

    assert result.exit_code == 1
    assert "widgetsource:widgets" in result.output
