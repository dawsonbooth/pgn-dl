"""Output layout: ``<out>/<source>/<kind>/<collection-slug>/<*segments>.pgn``."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from .io import write_text_atomic

COMBINED_NAME = "all.pgn"

#: Set this to an absolute path so ``login`` and downloads share a session even
#: when run from different directories.
PROFILE_ENV = "PGN_DL_PROFILE"


def profile_dir(source: str) -> Path:
    """Directory holding ``source``'s persistent session, under ``.profile``.

    One subdirectory per source: chess.com's is a Chrome profile, noctie's holds
    a refresh token as well. ``expanduser``, because a value set anywhere but an
    interactive shell arrives unexpanded; ``or``, because an empty value means
    unset rather than the working directory.
    """
    root = Path(os.environ.get(PROFILE_ENV) or ".profile").expanduser().resolve()
    return root / source

_SLUG_DROP = re.compile(r"[^a-zA-Z0-9]+")
_LEADING_INT = re.compile(r"\d+")


def slugify(s: str, fallback: str = "untitled") -> str:
    out = _SLUG_DROP.sub("-", s).strip("-").lower()
    return out or fallback


def collection_dir(out: Path, source: str, kind: str, slug: str) -> Path:
    return out / slugify(source) / slugify(kind) / slugify(slug)


def item_path(collection_dir: Path, segments: Sequence[str]) -> Path:
    """Path for one item. Segments are already slugified and numbered by the
    extractor; this numbering is the resume contract, so it lives in one place."""
    if not segments:
        raise ValueError("item needs at least one path segment")
    return collection_dir.joinpath(*segments[:-1]) / f"{segments[-1]}.pgn"


def _order_key(rel: Path) -> tuple[tuple[int, ...], str]:
    """Sort by the leading number of each path segment, so chapter 100 follows
    99 regardless of zero-pad width. Depth-agnostic: a flat collection has a
    one-element key, a course a two-element one."""

    def leading(name: str) -> int:
        m = _LEADING_INT.match(name)
        return int(m.group()) if m else 0

    return tuple(leading(p) for p in rel.parts), str(rel)


def combine_collection_dir(
    collection_dir: Path, *, echo: Callable[[str], None] = print
) -> Path:
    """Concatenate every item PGN under ``collection_dir`` into ``all.pgn``."""
    out_file = collection_dir / COMBINED_NAME
    # rglob: item files nest (a course's chapter directories, a book's
    # bookmarks/ subdirectory), and all.pgn is meant to hold all of them.
    files = sorted(
        (f for f in collection_dir.rglob("*.pgn") if f.is_file() and f.name != COMBINED_NAME),
        key=lambda f: _order_key(f.relative_to(collection_dir)),
    )
    # Everything pgn-dl wrote is UTF-8, so an unreadable file is someone else's
    # (a GUI's latin-1 export, say) and gets named and left out rather than
    # guessed at. A symlink is left out on the same terms: following one would
    # copy content from outside the tree into a file the user goes on to share.
    chunks: list[str] = []
    for f in files:
        if f.is_symlink():
            echo(f"  ! Leaving {f} out of {COMBINED_NAME}: it is a symbolic link")
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            echo(f"  ! Leaving {f} out of {COMBINED_NAME}: {exc}")
            continue
        if stripped := text.rstrip():
            chunks.append(stripped)
    write_text_atomic(out_file, "\n\n".join(chunks) + "\n")
    return out_file
