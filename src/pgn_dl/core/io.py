"""Filesystem primitives the resume contract and credential storage lean on."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from .fetch import FetchError


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 via a temp file + ``os.replace``.

    The destination therefore only ever appears complete, so an interrupted run
    can't leave a half-written PGN that the resume-by-existence check would skip.

    Credential storage leans on two more properties of this: ``mkstemp`` creates
    owner-only and ``os.replace`` carries that mode over, so the content is never
    readable by anyone else even for an instant, and the rename replaces a
    symlink at ``path`` rather than writing through it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _report_open_root(root: Path, echo: Callable[[str], None]) -> None:
    """Name the directory holding the profiles when others can reach it.

    A root that already existed keeps the mode it had, and narrowing it is not
    this tool's call: the profile location is the user's to choose, and one they
    pointed at a shared directory on purpose must not silently become owner-only.
    The profile inside it is 0700 either way, so what a loose root leaves open is
    which sources have a session here and, if it is writable, the chance for
    someone else to move that profile aside.
    """
    mode = stat.S_IMODE(root.stat().st_mode)
    if not mode & 0o077:
        return
    echo(f"  ! {root} is mode {mode:04o}, so other users can reach it.")
    echo(
        "    pgn-dl does not narrow a directory it did not create; chmod 700 it yourself."
    )


def private_dir(path: Path, *, echo: Callable[[str], None] = print) -> Path:
    """Create ``path`` owner-only, narrowing it if it already exists.

    Profile directories hold a browser session, and noctie's holds a refresh
    token twice over: once in credentials.json and once in Chrome's IndexedDB.
    ``mkdir``'s mode applies only on creation and is ignored for parents, so
    each level is made separately and an existing directory is narrowed.

    A symlink is refused rather than followed. The chmod would land on its
    target, and so would everything written underneath.
    """
    if path.is_symlink():
        raise FetchError(
            f"{path} is a symbolic link; refusing to use it as a profile directory"
        )
    for d in (*reversed(path.parents), path):
        d.mkdir(0o700, exist_ok=True)
    # Also covers a umask that stripped a bit the owner needs.
    os.chmod(path, 0o700)
    _report_open_root(path.parent, echo)
    return path
