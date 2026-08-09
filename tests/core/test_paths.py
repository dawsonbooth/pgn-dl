"""Output-path construction and combining, independent of any source."""

from __future__ import annotations

from pathlib import Path

from pgn_dl.core.paths import (
    collection_dir,
    combine_collection_dir,
    item_path,
    slugify,
)


def test_slugify_collapses_punctuation_and_falls_back() -> None:
    assert slugify("Aman Hambleton's London System") == "aman-hambleton-s-london-system"
    assert slugify("!!!", fallback="lesson") == "lesson"


def test_collection_dir_namespaces_by_source_and_kind() -> None:
    d = collection_dir(Path("output"), "chesscom", "courses", "London System")
    assert d == Path("output/chesscom/courses/london-system")


def test_item_path_joins_segments_and_appends_extension() -> None:
    d = Path("output/chesscom/courses/london")
    assert item_path(d, ("01-intro", "003-the-fork")) == d / "01-intro/003-the-fork.pgn"
    assert item_path(d, ("0001-a-game",)) == d / "0001-a-game.pgn"


def _write(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[Event "{marker}"]\n\n*\n', encoding="utf-8")


def test_combine_orders_by_number_not_lexically(tmp_path: Path) -> None:
    # Directories are zero-padded, but once a number overflows the pad width a
    # plain string sort misorders it (100 < 99). Combining must stay numeric.
    for chapter, marker in [("09-a", "AAA"), ("10-b", "BBB"), ("99-y", "YYY"), ("100-z", "ZZZ")]:
        _write(tmp_path / chapter / "001-l.pgn", marker)

    combined = combine_collection_dir(tmp_path).read_text(encoding="utf-8")
    positions = [combined.index(m) for m in ("AAA", "BBB", "YYY", "ZZZ")]
    assert positions == sorted(positions)


def test_combine_handles_flat_single_segment_collections(tmp_path: Path) -> None:
    # A source with no hierarchy writes items at the collection root, and those
    # have to reach all.pgn as well.
    for name, marker in [("001-a", "AAA"), ("002-b", "BBB")]:
        _write(tmp_path / f"{name}.pgn", marker)

    combined = combine_collection_dir(tmp_path).read_text(encoding="utf-8")
    assert combined.index("AAA") < combined.index("BBB")


def test_combine_never_includes_itself(tmp_path: Path) -> None:
    _write(tmp_path / "001-a.pgn", "AAA")
    first = combine_collection_dir(tmp_path).read_text(encoding="utf-8")
    second = combine_collection_dir(tmp_path).read_text(encoding="utf-8")
    assert first == second
    assert second.count("AAA") == 1


def test_combine_leaves_out_a_file_it_cannot_read(tmp_path: Path) -> None:
    # Combining is the last thing a run does, so failing here throws away a
    # whole run's report. The file need not even be ours: chess GUIs write
    # latin-1 PGN routinely.
    _write(tmp_path / "001-a.pgn", "AAA")
    (tmp_path / "002-gui.pgn").write_bytes(b'[Event "Caf\xe9"]\n\n*\n')
    _write(tmp_path / "003-b.pgn", "BBB")

    lines: list[str] = []
    combined = combine_collection_dir(tmp_path, echo=lines.append)
    text = combined.read_text(encoding="utf-8")

    assert text.index("AAA") < text.index("BBB")
    assert any("002-gui.pgn" in line for line in lines)


def test_combine_leaves_out_a_symbolic_link(tmp_path: Path) -> None:
    # all.pgn is built from the files pgn-dl wrote. Following a link copies
    # content from outside the tree into a file the user goes on to share.
    outside = tmp_path / "outside.pgn"
    outside.write_text('[Event "OUTSIDE"]\n\n*\n', encoding="utf-8")
    coll = tmp_path / "collection"
    _write(coll / "001-a.pgn", "AAA")
    (coll / "002-link.pgn").symlink_to(outside)

    lines: list[str] = []
    text = combine_collection_dir(coll, echo=lines.append).read_text(encoding="utf-8")

    assert "OUTSIDE" not in text
    assert "AAA" in text
    assert any("002-link.pgn" in line for line in lines)
