"""CLI surface: subcommands exist, URL sugar resolves, unknown input is rejected."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pgn_dl.cli import app

runner = CliRunner()


def test_root_help_lists_sources_and_login() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "chesscom" in result.output
    assert "login" in result.output
    assert "sources" in result.output


def test_sources_command_lists_registered_keys() -> None:
    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    assert "chesscom:courses" in result.output


def test_chesscom_courses_subcommand_exists() -> None:
    result = runner.invoke(app, ["chesscom", "courses", "--help"])
    assert result.exit_code == 0
    assert "--concurrency" in result.output
    assert "--delay" in result.output
    assert "--limit" in result.output
    assert "--out" in result.output


def test_unknown_url_reports_known_sources() -> None:
    # Bare-URL sugar: an unrecognized URL reaches the resolver, not Click's
    # unknown-command error, and the message names what IS supported.
    result = runner.invoke(app, ["https://example.com/nope"])
    assert result.exit_code != 0
    assert "chesscom:courses" in result.output


def test_explicit_url_command_reports_known_sources() -> None:
    result = runner.invoke(app, ["url", "https://example.com/nope"])
    assert result.exit_code != 0
    assert "chesscom:courses" in result.output


def test_bare_chesscom_course_url_resolves_to_the_courses_extractor() -> None:
    # Resolution happens before any network access, so this exercises dispatch
    # without touching chess.com.
    from pgn_dl.core.registry import resolve_url
    from pgn_dl.sources.chesscom.courses import CoursesExtractor

    url = "https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e"
    assert isinstance(resolve_url(url), CoursesExtractor)


def test_login_rejects_a_source_with_no_login() -> None:
    result = runner.invoke(app, ["login", "nosuchsource"])
    assert result.exit_code != 0


def test_a_login_that_fails_reports_it_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sign-in can time out or be abandoned; the command has to say so rather
    # than unwinding a FetchError through Typer.
    from pgn_dl.core.fetch import FetchError

    async def times_out() -> None:
        raise FetchError("timed out waiting for noctie sign-in")

    monkeypatch.setattr("pgn_dl.cli.logins", lambda: {"faketest": times_out})

    result = runner.invoke(app, ["login", "faketest"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "timed out" in result.output


def test_a_traceback_never_renders_local_variables() -> None:
    # A frame between the token exchange and the file it lands in holds the
    # refresh token. Typer's default is not to render locals; pin it, because
    # nothing else here would notice it changing.
    assert app.pretty_exceptions_show_locals is False


def test_bare_url_accepts_the_same_flags_as_the_url_command(tmp_path: Path) -> None:
    # The sugar reroutes the whole argv to `url`, so options parse there too.
    # Exit code 1 is the resolver rejecting the URL; a rejected option would be 2.
    result = runner.invoke(app, ["https://example.com/nope", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "chesscom:courses" in result.output


def test_a_mistyped_command_is_not_treated_as_a_url() -> None:
    result = runner.invoke(app, ["chesscomm"])
    assert result.exit_code != 0
    assert "no source handles" not in result.output


def test_no_arguments_prints_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_url_command_is_visible_in_help() -> None:
    # The bare-URL form is sugar over this command and forwards the same flags
    # to it, so keeping `url` listed is what makes those flags discoverable.
    result = runner.invoke(app, ["--help"])
    assert "url" in result.output


def test_every_source_registers_its_sub_app() -> None:
    # Sources already self-register their extractors and their login; the sub-
    # app is the third thing core would otherwise have to know each source by
    # name to wire up.
    from pgn_dl.core.registry import apps
    from pgn_dl.sources import chesscom, chessreps, noctie

    assert apps()["chesscom"] is chesscom.app
    assert apps()["chessreps"] is chessreps.app
    assert apps()["noctie"] is noctie.app


def test_the_root_app_mounts_the_registered_sources_in_order() -> None:
    # Alphabetical, so `--help` lists them the same way on every build whatever
    # order the source packages happened to be imported in.
    assert [group.name for group in app.registered_groups] == [
        "chessable",
        "chesscom",
        "chessreps",
        "noctie",
    ]


def test_mounting_is_driven_by_the_registry_not_by_hard_coded_imports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A source that core has never heard of must still be mounted. Patching the
    # registry accessor is what proves cli.py reads it rather than importing
    # two known sub-apps by name.
    import typer

    from pgn_dl.cli import mount_sources

    fake = typer.Typer(no_args_is_help=True, help="fake source")

    @fake.command(name="ping")
    def ping() -> None:
        """Do nothing at all."""

    monkeypatch.setattr("pgn_dl.cli.apps", lambda: {"zeta": fake, "alpha": fake})
    root = typer.Typer(add_completion=False)
    mount_sources(root)

    assert [group.name for group in root.registered_groups] == ["alpha", "zeta"]
    result = runner.invoke(root, ["zeta", "ping", "--help"])
    assert result.exit_code == 0


def test_the_source_command_names_are_unchanged() -> None:
    # Whatever the mounting does, these are what a user types.
    chesscom = runner.invoke(app, ["chesscom", "--help"])
    assert chesscom.exit_code == 0
    assert "courses" in chesscom.output
    assert "lessons" in chesscom.output

    noctie = runner.invoke(app, ["noctie", "--help"])
    assert noctie.exit_code == 0
    assert "themes" in noctie.output
    assert "opening-books" in noctie.output


def test_the_opening_books_help_describes_the_bookmark_files() -> None:
    # A book produces one PGN per bookmark under bookmarks/ as well as the whole
    # repertoire, and the help has to say so. Asserted on the docstring, not the
    # rendered page: rich rewraps the prose, so a sentence in the output is not
    # a contiguous substring of it.
    from pgn_dl.sources.noctie import opening_books_command

    doc = opening_books_command.__doc__ or ""
    assert "bookmarks/" in doc

    result = runner.invoke(app, ["noctie", "opening-books", "--help"])
    assert result.exit_code == 0
    assert "bookmark" in result.output


def test_chesscom_submodules_are_not_shadowed_by_same_named_commands() -> None:
    # `courses` and `lessons` are both Typer commands and submodules of the
    # chesscom package. Defining `def courses(...)` at module scope rebinds the
    # name to the command function, so `pgn_dl.sources.chesscom.lessons` would
    # resolve to the function instead of the module. `import
    # pgn_dl.sources.chesscom.lessons` still works, since sys.modules is
    # consulted, but `from pgn_dl.sources.chesscom import lessons` gets the
    # wrong object.
    from pgn_dl.sources import chesscom

    assert inspect.ismodule(chesscom.courses)
    assert inspect.ismodule(chesscom.lessons)
