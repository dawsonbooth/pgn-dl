"""Learn: the stages found in the Learn page's module, read with their English
text, and each written as a file of games, one per level.

The module is a miniature hand-assembled in the live minified format: the
level builder, both shape helpers, a defaults object and a defaults function,
stages defined in key order with one bound again under an alias, and the
categories list naming them in the curriculum's order. Positions and text are
invented. Nothing is captured.
"""

from __future__ import annotations

import json

import chess
import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.extractor import WholeSection
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import Collection
from pgn_dl.sources.lichess.client import Client
from pgn_dl.sources.lichess.learn import (
    Category,
    LearnExtractor,
    Level,
    Shape,
    Stage,
    Step,
    _prose,
    fetch_learn,
    layout,
    level_game,
    parse_strings,
    read_learn,
    stage_from,
    stage_pgn,
)
from pgn_dl.sources.lichess.script import ScriptError

from .conftest import ASSETS, ROOT, USER_AGENT, FakeFetcher, games

#: Learn's stage module in miniature; ``@n`` and ``@c`` name the arrow and
#: circle helpers, which the minifier renames from one build to the next.
MODULE = (
    'import{c as T}from"./lib.TQ2QZ3XA.js";import{b as d}from"./lib.UV7RKD4M.js";'
    'var be="/learn";'
    # The level builder every stage maps its levels through.
    'function t(e,a){return e.fen.split(" ").length===4&&(e.fen+=" 0 1"),'
    "{id:a+1,apples:[],color:T(e.fen),...e}}"
    'var o=document.body.dataset.assetUrl+"/assets/",'
    '@n=(e,a)=>({brush:a||"paleGreen",orig:e.slice(0,2),dest:e.slice(2,4)}),'
    '@c=(e,a)=>({brush:a||"green",orig:e}),'
    'g=e=>d("div.no-square",d("piece.white."+e)),'
    's=e=>d("div.round-svg",d("img",{attrs:{src:e}}));'
    # Success tests: code, which nothing reads.
    "var M=e=>e.chess.instance.isCheck(),f=e=>a=>!e(a),"
    "i=e=>e.scenario.isComplete(),p=e=>e.scenario.isFailed();"
    # Level defaults from a function, before the stage spreading them.
    'var Ie=()=>({nbMoves:1,color:"white",success:i,failure:p}),'
    'Ce={key:"enpassant",title:i18n.learn.enPassant,'
    "subtitle:i18n.learn.theSpecialPawnMove,"
    'image:o+"images/learn/spinning-blades.svg",intro:i18n.learn.enPassantIntro,'
    'illustration:s(o+"images/learn/spinning-blades.svg"),levels:['
    "{goal:i18n.learn.blackJustMovedThePawnByTwoSquares,"
    'fen:"4k3/5p2/8/4P3/8/8/8/4K3 b - -",'
    'scenario:[{move:"f7f5",shapes:[@n("e5f6")]},"e5f6"],captures:1},'
    "{goal:i18n.learn.enPassantOnlyWorksImmediately,"
    'fen:"4k3/2p5/8/3P4/8/8/8/4K3 b - -",shapes:[@c("c5","red")],'
    'scenario:["c7c5","d5c6"],captures:1}'
    "].map((e,a)=>t({...Ie(),...e,pointsForCapture:!0},a)),"
    "complete:i18n.learn.enPassantComplete};"
    # Level defaults as an object.
    "var Ne={goal:i18n.learn.checkInOneGoal,nbMoves:1,failure:f(M),success:M},"
    'Se={key:"check1",title:i18n.learn.checkInOne,'
    "subtitle:i18n.learn.attackTheOpponentsKing,"
    'image:o+"images/learn/winner.svg",intro:i18n.learn.checkInOneIntro,levels:['
    '{fen:"4k3/8/8/8/8/8/8/R6K w - -",shapes:[@n("a1a8")]},'
    "{goal:i18n.learn.attackYourOpponentsKing,"
    'fen:"4k3/8/8/8/8/8/1B6/7K w - - 0 1",detectCapture:!1}'
    "].map((e,a)=>t({...Ne,...e},a)),complete:i18n.learn.checkInOneComplete};"
    # A stage the categories name by the alias the minifier binds after it.
    'var Le={key:"rook",title:i18n.learn.theRook,'
    "subtitle:i18n.learn.itMovesInStraightLines,"
    'image:o+"images/learn/pieces/R.svg",intro:i18n.learn.rookIntro,'
    'illustration:g("rook"),levels:['
    '{goal:i18n.learn.rookGoal,fen:"8/8/8/8/8/8/4R3/8 w - -",apples:"e7",'
    'nbMoves:1,shapes:[@n("e2e7")]},'
    '{goal:i18n.learn.grabAllTheStars,fen:"8/8/8/8/3R4/8/8/8 w - -",'
    'apples:"a4 h8",nbMoves:3,shapes:[@n("d4a4","yellow"),@c("a1")]}'
    "].map(t),complete:i18n.learn.rookComplete},re=Le;"
    # The categories, naming their stages in the curriculum's order.
    'var Ee=[{key:"chess-pieces",name:i18n.learn.chessPieces,stages:[re]},'
    '{key:"fundamentals",name:i18n.learn.fundamentals,stages:[Se,Ce]}],'
    "Ve=1,me=Ee.map(e=>({...e,stages:e.stages.map(a=>({...a,id:Ve++}))}));"
    "export{be as a,Ee as b};"
)


def learn_script(arrow: str = "n", circle: str = "c") -> str:
    return MODULE.replace("@n", arrow).replace("@c", circle)


ENGLISH = {
    "chessPieces": "Chess pieces",
    "fundamentals": "Fundamentals",
    "theRook": "The rook",
    "itMovesInStraightLines": "It moves in straight lines",
    "rookIntro": "A rook runs along ranks and files.\nTake it for a spin.",
    "rookGoal": "Move the rook\nonto the star.",
    "grabAllTheStars": "Collect every star.",
    "rookComplete": "Well done: the rook is yours.",
    "checkInOne": "Check in one",
    "attackTheOpponentsKing": "Attack the king",
    "checkInOneIntro": "To check, attack the king.\nHe must get out of it.",
    "checkInOneGoal": "Check the king in one move.",
    "attackYourOpponentsKing": "Attack the king\nso that he cannot escape!",
    "checkInOneComplete": "Now you can give check.",
    "enPassant": "En passant",
    "theSpecialPawnMove": "The special pawn move",
    "enPassantIntro": "A pawn that has just moved two squares\ncan be taken "
    "as if it had moved one.",
    "blackJustMovedThePawnByTwoSquares": "Black has just moved\na pawn two "
    "squares.\nTake it en passant.",
    "enPassantOnlyWorksImmediately": "To take en passant:\n- take at once\n"
    "- from your fifth rank",
    "enPassantComplete": "You can take en passant.",
    "unused": "Nobody reads this.",
}


def strings_file(english: dict[str, str]) -> str:
    """Learn's strings file as Lichess's i18n build writes it."""
    entries = "".join(
        f"i['{key}']={json.dumps(text, ensure_ascii=False)};"
        for key, text in english.items()
    )
    return (
        '"use strict";(function(){function s(t){let e=(...n)=>l(t,...n).join("");'
        "return e.asArray=(...n)=>l(t,...n),e}function l(t,...e){return [t]}"
        "let i=window.i18n.learn={};" + entries + "i['stageX']=s(\"Stage %s\")})()"
    )


def categories(script: str | None = None) -> list[Category]:
    return read_learn(learn_script() if script is None else script, ENGLISH)


def stage(key: str, script: str | None = None) -> Stage:
    [found] = [s for c in categories(script) for s in c.stages if s.key == key]
    return found


def test_the_strings_file_s_plain_entries_are_read_unescaped() -> None:
    script = (
        "let i=window.i18n.learn={};i['a']=\"Plain\";"
        'i[\'b\']="say \\"hi\\"\\nthen go";i[\'c\']="back\\\\slash \\u00e9t\\u00e9";'
        "i['stageX']=s(\"Stage %s\");i['d']=\"It's done\"})()"
    )
    assert parse_strings(script) == {
        "a": "Plain",
        "b": 'say "hi"\nthen go',
        "c": "back\\slash été",
        "d": "It's done",
    }


def test_the_built_strings_file_reads_back_as_written() -> None:
    assert parse_strings(strings_file(ENGLISH)) == ENGLISH


# Reading the stages.


def test_the_stages_are_read_in_the_categories_order_and_numbered_across_them() -> None:
    # Defined in key order, listed in the curriculum's: the rook by its alias.
    assert [
        (c.key, c.name, [(s.id, s.key) for s in c.stages]) for c in categories()
    ] == [
        ("chess-pieces", "Chess pieces", [(1, "rook")]),
        ("fundamentals", "Fundamentals", [(2, "check1"), (3, "enpassant")]),
    ]


def test_a_stage_s_text_is_its_english_string_with_its_line_breaks() -> None:
    rook = stage("rook")
    assert (rook.title, rook.subtitle, rook.intro, rook.complete) == (
        "The rook",
        "It moves in straight lines",
        "A rook runs along ranks and files.\nTake it for a spin.",
        "Well done: the rook is yours.",
    )


def test_a_line_break_inside_a_sentence_is_a_space() -> None:
    goals = [level.goal for level in stage("enpassant").levels]
    assert goals == [
        "Black has just moved a pawn two squares.\nTake it en passant.",
        "To take en passant:\n- take at once\n- from your fifth rank",
    ]


def test_a_table_keeps_a_row_to_a_line() -> None:
    # Learn's piece values: "Queen = 9" and so on, each on its own line.
    text = "Pieces have values!\nQueen = 9\nRook = 5"
    assert _prose(text) == text


def test_levels_mapped_as_written_are_read_as_written() -> None:
    assert stage("rook").levels == (
        Level(
            id=1,
            goal="Move the rook onto the star.",
            fen="8/8/8/8/8/8/4R3/8 w - - 0 1",
            color=chess.WHITE,
            stars=("e7",),
            shapes=(Shape("e2", "e7", "paleGreen"),),
        ),
        Level(
            id=2,
            goal="Collect every star.",
            fen="8/8/8/8/3R4/8/8/8 w - - 0 1",
            color=chess.WHITE,
            stars=("a4", "h8"),
            shapes=(Shape("d4", "a4", "yellow"), Shape("a1", None, "green")),
        ),
    )


def test_defaults_spread_from_an_object_fill_in_what_a_level_leaves_out() -> None:
    first, second = stage("check1").levels
    assert first.goal == "Check the king in one move."
    assert second.goal == "Attack the king so that he cannot escape!"
    # A position with all six fields is left as it is.
    assert second.fen == "4k3/8/8/8/8/8/1B6/7K w - - 0 1"


def test_defaults_from_a_function_are_the_object_it_returns() -> None:
    # The defaults have the learner play White, though Black moves first.
    for level in stage("enpassant").levels:
        assert chess.Board(level.fen).turn == chess.BLACK
        assert level.color == chess.WHITE


def test_without_a_side_named_the_learner_plays_the_side_to_move() -> None:
    assert [level.color for level in stage("check1").levels] == [chess.WHITE] * 2


def test_scripted_moves_are_read_as_moves_or_as_moves_with_shapes() -> None:
    first, second = stage("enpassant").levels
    assert first.scenario == (
        Step("f7f5", (Shape("e5", "f6", "paleGreen"),)),
        Step("e5f6"),
    )
    assert second.scenario == (Step("c7c5"), Step("d5c6"))
    assert second.shapes == (Shape("c5", None, "red"),)


@pytest.mark.parametrize(("arrow", "circle"), [("Xe", "$k"), ("c", "n")])
def test_the_shape_helpers_are_found_by_what_they_do_not_by_name(
    arrow: str, circle: str
) -> None:
    assert categories(learn_script(arrow, circle)) == categories()


def test_a_shape_without_a_brush_takes_its_helper_s_default() -> None:
    script = learn_script().replace('brush:a||"paleGreen"', 'brush:a||"paleRed"')
    first, second = stage("rook", script).levels
    assert first.shapes == (Shape("e2", "e7", "paleRed"),)
    assert second.shapes[0] == Shape("d4", "a4", "yellow")  # one given is kept


OWN_GOAL = "Attack the king so that he cannot escape!"
DEFAULT_GOAL = "Check the king in one move."


@pytest.mark.parametrize(
    ("built", "goals"),
    [
        ("{...Ne,...e}", [DEFAULT_GOAL, OWN_GOAL]),
        ("{...e,...Ne}", [DEFAULT_GOAL, DEFAULT_GOAL]),
        ('{...Ne,...e,goal:"Fixed."}', ["Fixed.", "Fixed."]),
        ('{goal:"Fixed.",...e}', ["Fixed.", OWN_GOAL]),
    ],
)
def test_a_level_s_properties_merge_in_the_order_they_are_written(
    built: str, goals: list[str]
) -> None:
    script = learn_script().replace("t({...Ne,...e},a)", f"t({built},a)")
    assert [level.goal for level in stage("check1", script).levels] == goals


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('{brush:a||"green",orig:e}', '{brush:a||"green",at:e}', "no shape helpers"),
        ('[{key:"chess-pieces"', '[{key:"pieces"', "found no Learn categories"),
        (
            "stages:[Se,Ce]}]",
            "stages:[Se,Ce]},42]",
            "a Learn category is not an object",
        ),
        ("stages:[re]", "stages:[Le,he]", "Learn lists a stage it does not define"),
        ("stages:[re]", "stages:[]", "a Learn category lists no stages"),
        (
            "title:i18n.learn.theRook",
            "title:i18n.learn.theCastle",
            "no English string 'theCastle' for stage 'rook''s title",
        ),
        (
            '"8/8/8/8/8/8/4R3/8 w - -"',
            '"8/8/8/8/8/8/4R3 w - -"',
            "stage 'rook' level 1 has an unreadable position",
        ),
        ('apples:"e7"', 'apples:"e9"', "stage 'rook' level 1 has unreadable stars"),
        (
            'color:"white"',
            'color:"purple"',
            "stage 'enpassant' level 1 names an unknown side",
        ),
        (
            'c("a1")',
            'c("a1","purple")',
            "stage 'rook' level 2 has an unreadable shape",
        ),
        ('n("e2e7")', 'n("e2")', "stage 'rook' level 1 has an unreadable shape"),
        ('n("e2e7")', 'q("e2e7")', "stage 'rook' level 1 has an unreadable shape"),
        (
            ',"e5f6"]',
            ",42]",
            "stage 'enpassant' level 1 scripts an unreadable move",
        ),
        ("{...Ne,...e}", "{...Zz,...e}", "level defaults 'Zz' are not defined"),
        ("{...Ie(),", "{...Ie,", "Learn spreads a function as level defaults"),
        ("{...Ie(),", "{...Ie(1),", "a Learn level spreads an unknown value"),
        (
            "{goal:i18n.learn.rookGoal,",
            "{...x,goal:i18n.learn.rookGoal,",
            "a Learn level is not a plain object",
        ),
        (
            ".map((e,a)=>t({...Ne,...e},a))",
            ".map((e,a)=>e)",
            "a Learn stage builds its levels in an unknown way",
        ),
    ],
)
def test_a_module_in_a_shape_this_does_not_understand_fails(
    old: str, new: str, message: str
) -> None:
    script = learn_script()
    assert script.count(old) == 1, old
    with pytest.raises(ScriptError, match=message):
        categories(script.replace(old, new))


# Writing a stage.


COLLECTION = Collection("lichess", "learn", "chess-basics", "Chess basics")


def written(key: str) -> list[chess.pgn.Game]:
    """A stage as the file the extractor writes, read back game by game."""
    return games(stage_pgn(COLLECTION, stage(key)))


def test_each_level_is_a_game_of_its_position_named_for_its_stage() -> None:
    first, second = written("rook")
    assert dict(first.headers) == {
        "Event": "The rook",
        "Site": "lichess.org",
        "Date": "????.??.??",
        "Round": "1",
        "White": "?",
        "Black": "?",
        "Result": "*",
        "FEN": "8/8/8/8/8/8/4R3/8 w - - 0 1",
        "SetUp": "1",
    }
    assert (second.headers["Site"], second.headers["Round"]) == ("lichess.org", "2")
    assert second.headers["FEN"] == "8/8/8/8/3R4/8/8/8 w - - 0 1"


def test_the_first_level_opens_the_stage_and_the_last_closes_it() -> None:
    first, second = written("rook")
    assert first.comment == (
        "It moves in straight lines\n\n"
        "A rook runs along ranks and files.\nTake it for a spin.\n\n"
        "Move the rook onto the star. [%cal Ge2e7] [%csl Ye7]"
    )
    assert second.comment == (
        "Collect every star.\n\n"
        "Well done: the rook is yours. [%cal Yd4a4] [%csl Ga1,Ya4,Yh8]"
    )
    assert first.next() is None and second.next() is None


def test_stars_are_yellow_squares_and_hints_keep_their_colours() -> None:
    _, second = written("rook")
    drawn = [
        (chess.square_name(a.tail), chess.square_name(a.head), a.color)
        for a in second.arrows()
    ]
    assert drawn == [
        ("d4", "a4", "yellow"),  # a hint arrow, drawn yellow
        ("a1", "a1", "green"),  # a hint circle, the circle helper's default
        ("a4", "a4", "yellow"),  # the stars
        ("h8", "h8", "yellow"),
    ]


def test_scripted_moves_are_played_from_the_position_with_their_shapes() -> None:
    first, second = written("enpassant")
    assert [node.san() for node in first.mainline()] == ["f5", "exf6"]
    pushed = first.next()
    assert pushed is not None
    assert pushed.comment == "[%cal Ge5f6]"
    assert first.comment == (
        "The special pawn move\n\n"
        "A pawn that has just moved two squares can be taken as if it had "
        "moved one.\n\n"
        "Black has just moved a pawn two squares.\nTake it en passant."
    )
    assert first.end().comment == ""
    assert [node.san() for node in second.mainline()] == ["c5", "dxc6"]


def test_after_scripted_moves_the_stage_s_end_follows_the_last_move() -> None:
    _, last = written("enpassant")
    assert last.comment == (
        "To take en passant:\n- take at once\n- from your fifth rank [%csl Rc5]"
    )
    assert last.end().comment == "You can take en passant."


def test_a_level_both_first_and_last_carries_the_introduction_and_the_end() -> None:
    rook = stage("rook")
    item = level_game(rook, rook.levels[0], first=True, last=True)
    assert item.comment == (
        "It moves in straight lines\n\n"
        "A rook runs along ranks and files.\nTake it for a spin.\n\n"
        "Move the rook onto the star.\n\n"
        "Well done: the rook is yours."
    )


@pytest.mark.parametrize("move", ["d5e6", "c7c6", "zz", "0000"])
def test_a_scripted_move_that_cannot_be_played_fails_the_stage(move: str) -> None:
    script = learn_script().replace('"c7c5","d5c6"', f'"c7c5","{move}"')
    found = stage("enpassant", script)
    with pytest.raises(
        FetchError,
        match=rf"Learn 'enpassant' level 2 scripts '{move}', which cannot be played",
    ):
        level_game(found, found.levels[1], first=False, last=True)


# Finding the module.

STAGES_URL = f"{ASSETS}/lib.STAGES99.js"
MANIFEST = {
    "js": {
        "learn": {
            "hash": "LEARN0A1",
            "imports": ["lib.TQ2QZ3XA.js", "lib.STAGES99.js", "lib.UV7RKD4M.js"],
        },
        "learn.nvui": {"hash": "NVUI0001", "imports": []},
        "i18n/learn.en-GB": "c0ffee42",
        "i18n/learn.fr-FR": "f00dcafe",
    },
    "css": {"learn": "CSS00001"},
    "hashed": {},
}


def assets(**overrides: str) -> dict[str, str]:
    """The asset host: the manifest, the Learn page's modules, its strings."""
    files = {
        f"{ASSETS}/manifest.json": json.dumps(MANIFEST),
        f"{ASSETS}/learn.LEARN0A1.js": 'import{b as e}from"./lib.STAGES99.js";e();',
        f"{ASSETS}/lib.TQ2QZ3XA.js": 'var T=e=>e.split(" ")[1];export{T as c};',
        STAGES_URL: learn_script(),
        f"{ASSETS}/i18n/learn.en-GB.c0ffee42.js": strings_file(ENGLISH),
    }
    return files | overrides


async def test_the_manifest_leads_to_the_stages_module_then_the_strings() -> None:
    fetcher = FakeFetcher(assets())
    assert await fetch_learn(Client(fetcher)) == categories()
    assert fetcher.urls == [
        f"{ASSETS}/manifest.json",
        f"{ASSETS}/learn.LEARN0A1.js",
        f"{ASSETS}/lib.TQ2QZ3XA.js",
        STAGES_URL,
        f"{ASSETS}/i18n/learn.en-GB.c0ffee42.js",
    ]
    # Asset requests carry no token, and the manifest is asked for as JSON.
    assert all(h["User-Agent"] == USER_AGENT for _, _, h, _ in fetcher.requests)
    assert not any("Authorization" in h for _, _, h, _ in fetcher.requests)
    assert fetcher.requests[0][2].get("Accept") == "application/json"


async def test_stages_in_the_entry_module_itself_need_no_import() -> None:
    fetcher = FakeFetcher(assets(**{f"{ASSETS}/learn.LEARN0A1.js": learn_script()}))
    assert await fetch_learn(Client(fetcher)) == categories()
    assert fetcher.urls == [
        f"{ASSETS}/manifest.json",
        f"{ASSETS}/learn.LEARN0A1.js",
        f"{ASSETS}/i18n/learn.en-GB.c0ffee42.js",
    ]


@pytest.mark.parametrize(
    "manifest",
    [
        {"js": {"i18n/learn.en-GB": "c0ffee42"}},
        {"js": {"learn": {"hash": "LEARN0A1", "imports": []}}},
        {"js": {"learn": "LEARN0A1", "i18n/learn.en-GB": "c0ffee42"}},
        {"js": []},
        [],
    ],
)
async def test_a_manifest_naming_no_learn_module_fails(manifest: object) -> None:
    fetcher = FakeFetcher({f"{ASSETS}/manifest.json": json.dumps(manifest)})
    with pytest.raises(FetchError, match="manifest names no Learn module"):
        await fetch_learn(Client(fetcher))


async def test_no_module_holding_the_stages_fails() -> None:
    fetcher = FakeFetcher(
        assets(**{STAGES_URL: "var x=1;", f"{ASSETS}/lib.UV7RKD4M.js": "var y=2;"})
    )
    with pytest.raises(
        FetchError, match="no module of the Learn page holds its stages"
    ):
        await fetch_learn(Client(fetcher))


# Targets and the extractor.


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("https://lichess.org/learn#/3", 3),
        ("https://lichess.org/learn/#/3", 3),
        ("https://lichess.org/learn#/3/2", 3),
        ("https://lichess.org/learn#/12/", 12),
        ("  https://lichess.org/learn#/3\n", 3),
        # The query comes before the stage in a URL; the stage is still there.
        ("https://lichess.org/learn?utm_source=x#/3", 3),
    ],
)
def test_targets(target: str, expected: int) -> None:
    assert stage_from(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        "https://lichess.org/learn",
        "https://lichess.org/learn/",
        "https://lichess.org/learn#",
        "https://lichess.org/learn#/",
        "http://www.lichess.org/learn",
        "https://lichess.org/learn?utm_source=x",
    ],
)
def test_learn_s_own_url_is_refused(target: str) -> None:
    with pytest.raises(WholeSection, match="is all of Learn; give one stage's URL"):
        stage_from(target)


def test_the_registry_refuses_learn_s_own_url() -> None:
    with pytest.raises(WholeSection, match="is all of Learn"):
        registry.resolve_url("https://lichess.org/learn")


@pytest.mark.parametrize(
    "target",
    [
        "",
        "learn",
        "https://lichess.org/learning",
        "https://lichess.org/learn#/three",
        "https://lichess.org/practice",
        "https://example.com/learn",
    ],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(
        ValueError, match="could not read a lichess Learn stage"
    ) as refused:
        stage_from(target)
    assert not isinstance(refused.value, WholeSection)


def test_learn_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url("https://lichess.org/learn#/3")
    assert isinstance(found, LearnExtractor)
    assert found.stage == 3
    assert LearnExtractor.from_url("https://lichess.org/learnings") is None


async def test_each_stage_is_a_file_in_a_numbered_directory_per_category() -> None:
    categories = await fetch_learn(Client(FakeFetcher(assets())))
    assert [(stage.key, path) for stage, path in layout(categories)] == [
        ("rook", ("01-chess-pieces", "01-the-rook")),
        ("check1", ("02-fundamentals", "01-check-in-one")),
        ("enpassant", ("02-fundamentals", "02-en-passant")),
    ]


async def test_a_stage_s_url_downloads_that_stage_at_its_place() -> None:
    fetcher = FakeFetcher(assets())
    extractor = LearnExtractor(f"{ROOT}/learn#/3")
    collection, refs = await extractor.discover(fetcher)
    assert (collection.source, collection.kind) == ("lichess", "learn")
    assert (collection.slug, collection.name) == ("chess-basics", "Chess basics")
    assert [(r.id, r.path) for r in refs] == [
        ("enpassant", ("02-fundamentals", "02-en-passant"))
    ]
    asked = len(fetcher.requests)
    item = await extractor.fetch(fetcher, refs[0])
    assert len(fetcher.requests) == asked  # discovery already holds every stage
    assert item.pgn is not None
    played = games(item.pgn)
    assert [(g.headers["Event"], g.headers["Round"]) for g in played] == [
        ("En passant", "1"),
        ("En passant", "2"),
    ]
    assert played[-1].end().comment == "You can take en passant."


async def test_a_stage_learn_does_not_have_is_refused() -> None:
    with pytest.raises(FetchError, match="lichess Learn has no stage 4"):
        await LearnExtractor(f"{ROOT}/learn#/4").discover(FakeFetcher(assets()))
