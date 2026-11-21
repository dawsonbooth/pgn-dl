"""A drill page as a game: the walk-through, its comments, drawings and demos."""

from __future__ import annotations

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import Collection, GameItem
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.sources.chessendings.drill import drill_game, parse_drill

from .conftest import LUCENA_FEN, drill_props, games

COLLECTION = Collection(
    source="chessendings", kind="theory", slug="theory", name="Theory"
)


def _game(item: GameItem) -> chess.pgn.Game:
    (game,) = games(game_to_pgn(COLLECTION, item))
    return game


def _item(**overrides: object) -> GameItem:
    drill = parse_drill(drill_props(**overrides), "lesson x")
    return drill_game(drill, subject="lesson x", headers={"ChessendingsSlug": "x"})


def test_moves_and_replies_are_the_mainline() -> None:
    game = _game(_item())
    assert [m.uci() for m in game.mainline_moves()] == ["c1d1", "d8e7", "d1d4", "e7e6"]
    assert game.headers["FEN"] == LUCENA_FEN
    assert game.headers["Event"] == "The Lucena Position"
    assert game.headers["ChessendingsSlug"] == "x"
    # The student's side is named after the drill, the other left unknown.
    assert (game.headers["White"], game.headers["Black"]) == (
        "The Lucena Position",
        "?",
    )


def test_the_game_comment_is_the_task_the_intro_and_steps_before_any_move() -> None:
    game = _game(_item())
    assert game.comment == (
        "White to play and win.\n\nBuild a bridge.\n\n"
        "Never play Ra1?? It is stalemate. [%csl Yc7,Ya7]"
    )


def test_a_comment_and_its_drawings_sit_on_the_last_move_of_their_step() -> None:
    nodes = list(_game(_item()).mainline())
    ke7, ke6 = nodes[1], nodes[3]
    assert ke7.comment == "Rd1+, and his king steps away. [%csl Yc7]"
    assert nodes[0].comment == ""  # the move before the reply carries nothing
    # A zone is its squares in blue, and a line the piece watches is a blue arrow.
    assert ke6.comment == "Rd4 builds the bridge. [%cal Bd4b4] [%csl Ba4,Bb4,Ba5,Bb5]"


def test_a_demo_is_a_variation_from_where_it_starts() -> None:
    game = _game(_item())
    # The stalemate trap departs from the starting position, so it is an
    # alternative to the first move, after the mainline.
    assert [v.uci() for v in game.variations] == ["c1d1", "c1a1"]
    trap = game.variations[1]
    assert [trap.uci(), *(m.uci() for m in trap.mainline_moves())] == ["c1a1", "a2a1"]


def test_a_demo_sharing_the_mainline_follows_it_and_branches_where_it_departs() -> None:
    steps = [
        {"comment": "Rd1+.", "move": "c1d1", "reply": "d8e7"},
        {
            "comment": "Rd4, and if he takes a pawn later, Kc7 still wins.",
            "move": "d1d4",
            "reply": "e7e6",
            "demo": {"moves": ["b8c7", "a2c2"]},
        },
        {"comment": "Kc7.", "move": "b8c7", "reply": "a2a7"},
    ]
    game = _game(_item(lesson=steps))
    kc7 = list(game.mainline())[4]
    assert kc7.move.uci() == "b8c7"
    # One Kc7, not two: the demo joins it and departs at the reply.
    assert [v.move.uci() for v in kc7.variations] == ["a2a7", "a2c2"]


def test_a_demo_after_the_last_move_continues_the_mainline() -> None:
    steps = [
        {"comment": "Rd1+.", "move": "c1d1", "reply": "d8e7"},
        {"comment": "Then the rest.", "demo": {"moves": ["d1d4", "e7e6"]}},
    ]
    game = _game(_item(lesson=steps))
    assert [m.uci() for m in game.mainline_moves()] == ["c1d1", "d8e7", "d1d4", "e7e6"]
    assert list(game.mainline())[1].comment == "Rd1+.\n\nThen the rest."


def test_a_marked_square_outranks_a_zone_over_it() -> None:
    steps = [
        {
            "comment": "Here.",
            "move": "c1d1",
            "reply": "d8e7",
            "zones": [{"from": "c7", "to": "d8"}],
            "highlightSquares": ["c7"],
        }
    ]
    ke7 = list(_game(_item(lesson=steps)).mainline())[1]
    assert ke7.comment == "Here. [%csl Yc7,Bd7,Bc8,Bd8]"


def test_intro_squares_are_drawn_on_the_starting_position() -> None:
    game = _game(_item(lesson=[], introHighlightSquares=["b7"]))
    assert game.comment == "White to play and win.\n\nBuild a bridge. [%csl Yb7]"


def test_a_drill_without_steps_is_its_position_and_comment() -> None:
    props = drill_props(goal="draw")
    del props["lesson"]
    game = _game(
        drill_game(
            parse_drill(props, "x"), subject="x", headers={}, preface="From A - B."
        )
    )
    assert game.comment == "White to play and draw.\n\nFrom A - B.\n\nBuild a bridge."
    assert list(game.mainline_moves()) == []


def test_black_drills_name_black() -> None:
    props = drill_props(
        initialFen="8/8/8/8/P4k2/8/8/K7 b - - 0 1",
        initialHeroColor="b",
        goal="draw",
        lesson=[],
    )
    game = _game(drill_game(parse_drill(props, "x"), subject="x", headers={}))
    assert (game.headers["White"], game.headers["Black"]) == (
        "?",
        "The Lucena Position",
    )
    assert game.comment.startswith("Black to play and draw.")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"title": ""}, "no title"),
        ({"initialFen": None}, "no starting position"),
        ({"initialFen": "not a fen"}, "unreadable starting position"),
        ({"goal": "lose"}, "no side to play or no goal"),
        ({"initialHeroColor": "b"}, "side not to move"),
        ({"lesson": "steps"}, "not a list of steps"),
        ({"lesson": ["step"]}, "step 1 is not an object"),
        ({"lesson": [{"comment": "x", "highlightSquares": ["z9"]}]}, "not a square"),
        ({"lesson": [{"comment": "x", "zones": [{"from": "a1"}]}]}, "not squares"),
        ({"lesson": [{"comment": "x", "demo": {"moves": "c1d1"}}]}, "demo without"),
        ({"lesson": [{"comment": "x", "move": 12}]}, "not a UCI string"),
    ],
)
def test_a_drill_that_cannot_be_read_is_refused(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(FetchError, match=message):
        parse_drill(drill_props(**overrides), "lesson x")


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"comment": "x", "move": "c1c9"}], "unreadable move 'c1c9'"),
        ([{"comment": "x", "move": "c1d1", "reply": "d8d6"}], "d8d6, illegal"),
        (
            [{"comment": "x", "demo": {"moves": ["c1a1", "a2b3"]}}],
            "step 1 demo plays a2b3",
        ),
    ],
)
def test_an_unplayable_move_fails_the_drill_rather_than_cutting_it_short(
    steps: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(FetchError, match=message):
        _item(lesson=steps)
