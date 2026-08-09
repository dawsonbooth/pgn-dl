"""Atomic writes and owner-only directories."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

import pgn_dl.core.io as io_module
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.io import private_dir, write_text_atomic


def _interrupt_the_rename(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill the write at the last possible moment: the rename itself.

    ``KeyboardInterrupt`` because that is the interruption the module exists to
    survive, and because it is not an ``Exception``: a cleanup narrowed to
    ``except Exception`` would leave the temp file behind.
    """

    def interrupted(src: object, dst: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(io_module.os, "replace", interrupted)


def test_an_interrupted_write_leaves_no_file_at_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resume skips an item whose file exists, so a half-written PGN left behind
    # is never retried and never noticed.
    path = tmp_path / "001-item.pgn"
    _interrupt_the_rename(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        write_text_atomic(path, '[Event "C"]\n\n1. e4 *\n')

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []  # and no temp debris either


def test_an_interrupted_rewrite_leaves_the_previous_content_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.json"
    write_text_atomic(path, '{"refresh_token": "old"}')
    _interrupt_the_rename(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        write_text_atomic(path, '{"refresh_token": "new"}')

    assert path.read_text(encoding="utf-8") == '{"refresh_token": "old"}'
    assert [p.name for p in tmp_path.iterdir()] == ["credentials.json"]


def test_an_atomic_write_replaces_a_symlink(tmp_path: Path) -> None:
    # What save_refresh_token leans on: a planted link must not redirect the
    # content out of the profile directory.
    outside = tmp_path / "outside.json"
    outside.write_text("untouched", encoding="utf-8")
    link = tmp_path / "credentials.json"
    link.symlink_to(outside)

    write_text_atomic(link, "written")

    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == "written"
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_an_atomic_write_never_widens_the_file_it_creates(tmp_path: Path) -> None:
    # The destination is owner-only from the moment it exists, because the temp
    # file it is renamed from was.
    path = tmp_path / "nested" / "credentials.json"
    write_text_atomic(path, "{}")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_profile_directory_and_its_parents_are_owner_only(tmp_path: Path) -> None:
    # A browser profile holds session cookies; noctie's holds a refresh token
    # twice over. mkdir's mode is ignored for parents, so each level is made
    # separately.
    d = private_dir(tmp_path / "profile" / "chesscom")
    for level in (d, d.parent):
        mode = stat.S_IMODE(level.stat().st_mode)
        assert mode == 0o700, f"{level} is {mode:o}"


def test_an_existing_world_readable_profile_directory_is_narrowed(tmp_path: Path) -> None:
    d = tmp_path / "profile" / "chesscom"
    d.mkdir(parents=True)
    d.chmod(0o755)
    private_dir(d)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_a_pre_existing_profile_root_that_others_can_reach_is_named(
    tmp_path: Path,
) -> None:
    # mkdir's mode applies only to the levels pgn-dl creates, so a root the user
    # made themselves keeps whatever their umask gave it. Narrowing it is not
    # this tool's call - PGN_DL_PROFILE can point at a directory that is shared
    # on purpose - so it says so and leaves the decision to the owner.
    root = tmp_path / "profile"
    root.mkdir(mode=0o755)

    lines: list[str] = []
    private_dir(root / "chesscom", echo=lines.append)

    assert any(str(root) in line and "0755" in line for line in lines)


def test_a_profile_root_pgn_dl_created_itself_says_nothing(tmp_path: Path) -> None:
    # Every level it creates is 0700 already, and a warning on every run would
    # train the user to ignore the one that matters.
    lines: list[str] = []
    private_dir(tmp_path / "profile" / "chesscom", echo=lines.append)
    assert lines == []


def test_a_symlinked_profile_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    link = tmp_path / "profile"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(FetchError):
        private_dir(link)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
