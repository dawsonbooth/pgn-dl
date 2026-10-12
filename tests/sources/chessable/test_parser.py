"""Parser tests for the chessable source, against synthetic getCourse/getGame
payloads shaped exactly like the real API (captured 2026-08-09) but carrying
invented moves and prose, so no course content is committed.
"""

from __future__ import annotations

import json

import chess

from pgn_dl.sources.chessable import parser

STD = chess.STARTING_FEN


def _blob(data: list[dict]) -> str:
    """One node ``before``/``after`` field: a JSON string wrapping a data list."""
    return json.dumps({"before": "fen", "after": "fen", "data": data})


def _node(
    *,
    id: int,
    move: int,
    col: str,
    san: str,
    mid: int = 0,
    ann1: str | None = "",
    ann2: str = "",
    draws: list[dict] | None = None,
    before: list[dict] | None = None,
    after: list[dict] | None = None,
) -> dict:
    return {
        "id": id,
        "mid": mid,
        "move": move,
        "col": col,
        "san": san,
        "isKey": True,
        "class": "",
        "ann1": ann1,
        "ann2": ann2,
        "draws": draws or [],
        "before": _blob(before or []),
        "after": _blob(after or []),
        "lastUpdated": None,
    }


def _game(
    data: list[dict],
    *,
    initial: str = STD,
    is_info: int = 0,
    is_alt: int = 0,
    result: str = "*",
    title: str = "A Variation",
) -> dict:
    return {
        "game": {
            "owned": True,
            "data": data,
            "variant": None,
            "isInfo": is_info,
            "isRef": 0,
            "isAlt": is_alt,
            "initial": initial,
            "color": "white",
            "result": result,
            "title": title,
            "lineName": "A Chapter",
            "name": "A Course",
        },
        "hash": "191",
    }


def _course(chapters: list[dict], **over: object) -> dict:
    data = {
        "owned": True,
        "name": "A Course",
        "type": "opening",
        "isAuthor": False,
        "isRepertoire": True,
        "data": chapters,
        **over,
    }
    return {"course": data, "hash": "191"}


def _chapter(cid: int, title: str, variations: list[dict]) -> dict:
    return {"id": cid, "title": title, "order": cid, "variations": variations}


def _var(oid: int, name: str, type_: str) -> dict:
    return {"oid": oid, "name": name, "type": type_}


def test_parse_course_reads_name_and_ownership() -> None:
    outline = parser.parse_course(_course([]))
    assert outline.name == "A Course"
    assert outline.owned is True


def test_parse_course_extracts_chapters_and_variations_in_order() -> None:
    outline = parser.parse_course(
        _course(
            [
                _chapter(0, "Intro", [_var(10, "Welcome", "informational")]),
                _chapter(
                    1,
                    "Level 1",
                    [
                        _var(11, "Game #1", "puzzles"),
                        _var(12, "Sideline", "alternative"),
                    ],
                ),
            ]
        )
    )
    assert [c.title for c in outline.chapters] == ["Intro", "Level 1"]
    lvl1 = outline.chapters[1]
    assert [(v.oid, v.name) for v in lvl1.variations] == [
        ("11", "Game #1"),
        ("12", "Sideline"),
    ]


def test_parse_game_builds_the_linear_mainline() -> None:
    game = _game(
        [
            _node(id=0, mid=0, move=1, col="w", san="e4"),
            _node(id=1, mid=0, move=1, col="b", san="c5"),
            _node(id=2, mid=1, move=2, col="w", san="Nf3"),
        ]
    )
    content = parser.parse_game(game)
    assert content.starting_fen == STD
    sans = []
    node = content.root
    while node.children:
        node = node.children[0]
        sans.append(node.san)
    assert sans == ["e4", "c5", "Nf3"]


def test_parse_game_carries_the_starting_fen_for_a_puzzle() -> None:
    fen = "r1bqkbnr/pp1p1ppp/2n1p3/8/3PP3/5N2/PP3PPP/RNBQKB1R b KQkq - 0 5"
    game = _game([_node(id=0, mid=0, move=5, col="b", san="a6")], initial=fen)
    content = parser.parse_game(game)
    assert content.starting_fen == fen
    assert content.root.children[0].san == "a6"


def test_a_move_whose_drawings_are_null_has_none() -> None:
    node = _node(id=0, move=1, col="w", san="e4")
    node["draws"] = None

    move = parser.parse_game(_game([node])).root.children[0]

    assert (move.san, move.arrows, move.highlights) == ("e4", [], [])


def test_null_names_in_a_course_outline_read_as_absent_not_as_none() -> None:
    # str() of a null is "None", which would become the Event header and the
    # course's directory, and the chapter's and the variation's file names.
    raw = _course([_chapter(1, "Level 1", [_var(11, "Game #1", "puzzles")])])
    raw["course"]["name"] = None
    raw["course"]["data"][0]["title"] = None
    raw["course"]["data"][0]["variations"][0]["name"] = None

    outline = parser.parse_course(raw)

    assert outline.name == ""
    assert outline.chapters[0].title == ""
    assert outline.chapters[0].variations[0].name == ""


def test_a_chapter_whose_variations_are_null_has_none() -> None:
    empty = _chapter(2, "Level 2", [])
    empty["variations"] = None
    raw = _course([_chapter(1, "Level 1", [_var(11, "Game #1", "puzzles")]), empty])

    outline = parser.parse_course(raw)

    assert [len(c.variations) for c in outline.chapters] == [1, 0]


def test_a_before_comment_becomes_a_pre_comment_and_after_a_post_comment() -> None:
    game = _game(
        [
            _node(
                id=0,
                mid=0,
                move=1,
                col="w",
                san="e4",
                before=[{"key": "C", "state": "", "val": "Said before the move."}],
                after=[{"key": "C", "state": "", "val": "Said after the move."}],
            )
        ]
    )
    move = parser.parse_game(game).root.children[0]
    assert move.pre_comment == "Said before the move."
    assert move.post_comment == "Said after the move."


def test_comment_markers_and_html_are_reduced_to_plain_text() -> None:
    val = (
        "We are up @@StartBracket@@+1@@EndBracket@@ after "
        '...@@SANStart@@a6@@SANEnd@@, so <span class="italic">play</span> '
        "<strong>d5</strong>.<br/>@@StartBlockQuote@@Time is soul.@@EndBlockQuote@@"
    )
    game = _game(
        [
            _node(
                id=0, mid=0, move=1, col="w", san="e4", after=[{"key": "C", "val": val}]
            )
        ]
    )
    text = parser.parse_game(game).root.children[0].post_comment
    assert "@@" not in text
    assert "<span" not in text and "<strong>" not in text
    assert "(+1)" in text
    assert "...a6" in text
    assert "play d5." in text
    assert "Time is soul." in text


def test_ann1_glyphs_become_nags() -> None:
    game = _game(
        [
            _node(id=0, mid=0, move=1, col="w", san="e4", ann1="?"),
            _node(id=1, mid=0, move=1, col="b", san="c5", ann1="±"),
        ]
    )
    root = parser.parse_game(game).root
    assert root.children[0].nags == [2]  # ?
    assert root.children[0].children[0].nags == [16]  # ±


def test_a_move_whose_glyph_is_null_has_no_nags() -> None:
    game = _game([_node(id=0, mid=0, move=1, col="w", san="e4", ann1=None)])

    move = parser.parse_game(game).root.children[0]

    assert (move.san, move.nags) == ("e4", [])


def test_draws_become_arrows_and_highlights_with_colours() -> None:
    draws = [
        {"object": "arrow", "start": "d2", "end": "d4", "color": "g", "move": "1w"},
        {"object": "circle", "start": "c6", "color": "b", "move": "1w"},
        {"object": "circle", "start": "e1", "color": "r", "move": "1w"},
    ]
    game = _game([_node(id=0, mid=0, move=1, col="w", san="e4", draws=draws)])
    move = parser.parse_game(game).root.children[0]
    assert [(a.from_square, a.to_square, a.type) for a in move.arrows] == [
        ("d2", "d4", "green")
    ]
    assert [(h.square, h.type) for h in move.highlights] == [
        ("c6", "blue"),
        ("e1", "red"),
    ]


def test_a_leading_null_move_becomes_the_game_description() -> None:
    game = _game(
        [
            _node(
                id=0,
                mid=0,
                move=1,
                col="w",
                san="--",
                before=[{"key": "C", "val": "A pithy epigraph."}],
                after=[{"key": "C", "val": "The long introduction."}],
            )
        ],
        is_info=1,
    )
    content = parser.parse_game(game)
    assert "A pithy epigraph." in content.description
    assert "The long introduction." in content.description
    assert content.root.children == []  # prose only, no moves


def test_a_variation_inside_a_comment_becomes_a_real_pgn_variation() -> None:
    # After 1.e4, an after-comment holds "the game continued 1...c5 2.Nf3".
    v_items = [
        {"key": "C", "val": "The game continued:"},
        {"key": "S", "val": "1...c5"},
        {"key": "S", "val": "2.Nf3"},
    ]
    game = _game(
        [
            _node(
                id=0,
                mid=0,
                move=1,
                col="w",
                san="e4",
                after=[{"key": "V", "val": v_items}],
            )
        ]
    )
    e4 = parser.parse_game(game).root.children[0]
    # The sideline replies to e4, so it hangs below it as a continuation line.
    assert [c.san for c in e4.children] == ["c5"]
    assert e4.children[0].children[0].san == "Nf3"


def test_an_unplayable_variation_is_kept_as_comment_text_not_dropped() -> None:
    v_items = [
        {"key": "C", "val": "Loosely:"},
        {"key": "S", "val": "17...Qxh2"},  # cannot be played from the start
    ]
    game = _game(
        [
            _node(
                id=0,
                mid=0,
                move=1,
                col="w",
                san="e4",
                after=[{"key": "V", "val": v_items}],
            )
        ]
    )
    e4 = parser.parse_game(game).root.children[0]
    assert e4.children == []  # not attached as moves
    assert "Qxh2" in e4.post_comment  # kept as readable text instead


def test_an_unplayable_mainline_move_fails_the_item() -> None:
    game = _game([_node(id=0, mid=0, move=1, col="w", san="Qh5xh7")])
    try:
        parser.parse_game(game)
    except Exception as exc:  # noqa: BLE001
        assert "Qh5xh7" in str(exc)
    else:
        raise AssertionError("an unplayable mainline move must fail the item")
