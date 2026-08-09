"""Serializing a GameItem to PGN, independent of any source."""

from __future__ import annotations

import io

import chess
import chess.pgn
import pytest

from pgn_dl.core.models import Arrow, Collection, GameItem, Highlight, MoveNode
from pgn_dl.core.pgn import game_to_pgn

COLLECTION = Collection(
    source="fake",
    kind="things",
    slug="test-collection",
    name="Test Collection",
    headers={"Event": "Test Collection", "Site": "example.com"},
)


def _item(
    root: MoveNode,
    *,
    headers: dict[str, str] | None = None,
    comment: str = "",
    starting_fen: str = chess.STARTING_FEN,
) -> GameItem:
    return GameItem(
        headers=headers or {},
        starting_fen=starting_fen,
        comment=comment,
        root=root,
    )


def test_collection_headers_apply_and_item_headers_win() -> None:
    item = _item(MoveNode(san=None), headers={"Round": "1.1", "Site": "override"})
    pgn = game_to_pgn(COLLECTION, item)
    assert '[Event "Test Collection"]' in pgn
    assert '[Round "1.1"]' in pgn
    assert '[Site "override"]' in pgn


def test_game_with_no_moves_emits_comment_and_result() -> None:
    item = _item(MoveNode(san=None), comment="An introduction.")
    pgn = game_to_pgn(COLLECTION, item)
    assert "An introduction." in pgn
    assert pgn.rstrip().endswith("*")


def test_non_standard_start_emits_fen_and_setup() -> None:
    fen = "8/8/8/8/8/5k2/6q1/7K b - - 0 1"
    item = _item(MoveNode(san=None), starting_fen=fen)
    pgn = game_to_pgn(COLLECTION, item)
    assert f'[FEN "{fen}"]' in pgn
    assert '[SetUp "1"]' in pgn


def test_standard_start_omits_fen() -> None:
    item = _item(MoveNode(san=None), starting_fen=chess.STARTING_FEN)
    assert "[FEN " not in game_to_pgn(COLLECTION, item)


def test_first_child_is_mainline_and_siblings_are_variations() -> None:
    root = MoveNode(
        san=None,
        children=[
            MoveNode(san="e4", children=[MoveNode(san="e5")]),
            MoveNode(san="d4"),
        ],
    )
    reparsed = chess.pgn.read_game(io.StringIO(game_to_pgn(COLLECTION, _item(root))))
    assert reparsed is not None
    assert [v.san() for v in reparsed.variations] == ["e4", "d4"]


def test_drawings_and_comments_become_cal_csl_commands() -> None:
    root = MoveNode(
        san=None,
        children=[
            MoveNode(
                san="e4",
                pre_comment="before",
                post_comment="after",
                nags=[1],
                arrows=[Arrow(from_square="d2", to_square="d4", type="primary")],
                highlights=[Highlight(square="e4", type="tertiary")],
            )
        ],
    )
    pgn = game_to_pgn(COLLECTION, _item(root))
    assert "before" in pgn
    assert "after" in pgn
    assert "[%cal Gd2d4]" in pgn
    assert "[%csl Re4]" in pgn
    assert "$1" in pgn


def test_braces_in_a_comment_become_parentheses_rather_than_vanishing() -> None:
    # PGN delimits a comment with braces and gives no way to escape one inside,
    # so python-chess's exporter deletes a `}` and keeps a `{`: the file stays
    # structurally valid and re-parses without errors, and the text is quietly
    # mangled. Substituting keeps every character visible and balanced.
    # A distinct text per slot, and the starting comment on a sibling variation
    # rather than a mainline continuation: adjacent comments get merged on the
    # way back in, and then no assertion could say which slot kept what.
    root = MoveNode(
        san=None,
        children=[
            MoveNode(san="e4", post_comment="post {x} y }} z"),
            MoveNode(san="d4", pre_comment="pre {a} b"),
        ],
    )
    pgn = game_to_pgn(COLLECTION, _item(root, comment="game {intro} }"))

    # Three comments, so three pairs of delimiters and no brace besides them.
    body = pgn.split("]\n\n", 1)[1]
    assert body.count("{") == body.count("}") == 3
    assert "game (intro) )" in pgn

    reparsed = chess.pgn.read_game(io.StringIO(pgn))
    assert reparsed is not None
    assert reparsed.errors == []
    assert reparsed.comment == "game (intro) )"
    assert reparsed.variations[0].comment == "post (x) y )) z"
    assert reparsed.variations[1].starting_comment == "pre (a) b"


def test_a_brace_does_not_survive_into_a_drawing_annotated_comment() -> None:
    # The %cal/%csl commands are appended to the same comment, so a stray brace
    # in the prose would take them with it.
    root = MoveNode(
        san=None,
        children=[
            MoveNode(
                san="e4",
                post_comment="a }{ b",
                arrows=[Arrow(from_square="d2", to_square="d4", type="primary")],
            )
        ],
    )
    reparsed = chess.pgn.read_game(io.StringIO(game_to_pgn(COLLECTION, _item(root))))
    assert reparsed is not None
    assert reparsed.variations[0].comment == "a )( b [%cal Gd2d4]"


def test_an_unplayable_san_raises_rather_than_dropping_a_subtree() -> None:
    # Skipping the node takes everything below it as well, and the runner writes
    # and counts the shortened game that comes back.
    root = MoveNode(
        san=None,
        children=[
            MoveNode(san="Qz9", children=[MoveNode(san="e5")]),
            MoveNode(san="e4"),
        ],
    )
    with pytest.raises(ValueError) as exc:
        game_to_pgn(COLLECTION, _item(root))
    assert "Qz9" in str(exc.value)


def test_a_child_without_a_move_raises() -> None:
    # Only the root has no SAN; a child that has none names no move to write.
    root = MoveNode(san=None, children=[MoveNode(san=None)])
    with pytest.raises(ValueError):
        game_to_pgn(COLLECTION, _item(root))


def test_a_newline_in_a_header_cannot_split_the_file_into_two_games() -> None:
    # Names come from upstream free text. PGN ends a header line at the newline,
    # so the remainder starts a second game, one the runner counts as a single
    # clean write and every later run skips.
    root = MoveNode(san=None, children=[MoveNode(san="e4")])
    item = _item(root, headers={"White": 'Ivanov\n[Result "1-0"]\n', "Round": "1"})
    pgn = game_to_pgn(COLLECTION, item)

    stream = io.StringIO(pgn)
    first = chess.pgn.read_game(stream)
    assert first is not None
    assert chess.pgn.read_game(stream) is None
    assert first.headers["Result"] == "*"
    assert first.headers["White"].startswith("Ivanov")
    assert "\n" not in first.headers["White"]


def test_a_quote_or_backslash_in_a_header_is_escaped() -> None:
    # PGN does define escapes for these two, unlike the braces around a comment,
    # so nothing has to be substituted away.
    root = MoveNode(san=None, children=[MoveNode(san="e4")])
    item = _item(root, headers={"Event": r'The "Big" C:\chess'})
    pgn = game_to_pgn(COLLECTION, item)

    assert r'[Event "The \"Big\" C:\\chess"]' in pgn
    stream = io.StringIO(pgn)
    assert chess.pgn.read_game(stream) is not None
    assert chess.pgn.read_game(stream) is None
