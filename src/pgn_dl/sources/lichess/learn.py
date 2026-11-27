"""Lichess Learn ("Chess basics").

Learn is 18 stages in four categories (the pieces, fundamentals, intermediate,
advanced), each stage a handful of levels: a position, a goal ("Grab all the
stars!"), sometimes hint arrows, and for some levels the moves they script.
It is a client-side app, so none of it is served as data: the stages are
object literals in one of the Learn page's JavaScript modules and the text is
in its strings file, both on Lichess's asset host, where ``manifest.json``
names the current file of every module (see ``script.py`` for the reading).

Each stage is one file with a game per level, in a directory per category:

- the level's position, and its goal as the game comment, the stage's
  tagline and introduction before the first and its closing line after the
  last;
- the stars to collect as yellow squares, the hint shapes as arrows and
  circles in Lichess's colours;
- the moves, where the level scripts them (en passant, stalemate, piece value
  and a few others). Most levels leave the route to the player, and those are
  a position with no moves.

Most levels are a lone piece on an empty board, collecting stars or capturing:
positions without a king, which python-chess reads and plays but many GUIs
refuse to open. They are written as Lichess has them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import chess

from ...core.extractor import Extractor, WholeSection
from ...core.fetch import Fetcher, FetchError
from ...core.models import Arrow, Collection, GameItem, Highlight, ItemRef, MoveNode
from ...core.paths import slugify
from ...core.pgn import game_to_pgn
from .client import ASSETS, SITE, Client
from .script import Arrow as ArrowFn
from .script import Call, Mapped, Name, Obj, Reader, ScriptError, Spread

MANIFEST_URL = f"{ASSETS}/manifest.json"
COLLECTION_NAME = "Chess basics"

#: Where the stage data starts: the list of categories, the pieces first.
MARKER = '[{key:"chess-pieces"'
#: The strings file, in Lichess's source language.
STRINGS = "i18n/learn.en-GB"
_STRING = re.compile(r"i\['(\w+)'\]=\"((?:[^\"\\]|\\.)*)\"")
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.DOTALL)

#: The keys read anywhere in a category, stage, level, scenario step or shape.
#: Every other value is code (a level's success test) and is skipped unread.
KEEP = frozenset(
    {
        # category
        "key",
        "name",
        "stages",
        # stage
        "title",
        "subtitle",
        "intro",
        "levels",
        "complete",
        # level
        "goal",
        "fen",
        "apples",
        "shapes",
        "scenario",
        "color",
        # scenario step
        "move",
    }
)

#: The two shape helpers, ``n("f3d5", "red")`` and ``c("e1")``, found by their
#: definitions since minification renames them, with their default brush.
_ARROW_HELPER = re.compile(
    r"([\w$]+)=\(([\w$]+),([\w$]+)\)=>\(\{brush:\3\|\|\"(\w+)\","
    r"orig:\2\.slice\(0,2\),dest:\2\.slice\(2,4\)\}\)"
)
_CIRCLE_HELPER = re.compile(
    r"([\w$]+)=\(([\w$]+),([\w$]+)\)=>\(\{brush:\3\|\|\"(\w+)\",orig:\2\}\)"
)
#: A stage's object, bound to a name: ``he={key:"bishop",title:``.
_STAGE = re.compile(r"(?<![\w$.])([\w$]+)=\{key:\"[\w-]+\",title:")

#: chessground's brushes as pgn-dl's drawing colours; the pale ones are the
#: same colours drawn lighter.
_BRUSHES = {
    "green": "green",
    "paleGreen": "green",
    "red": "red",
    "paleRed": "red",
    "yellow": "yellow",
    "blue": "blue",
    "paleBlue": "blue",
}
#: The stars, which Learn draws as gold stars, as squares.
STAR = "yellow"


@dataclass(frozen=True)
class Shape:
    orig: str
    dest: str | None  # None for a circle
    brush: str


@dataclass(frozen=True)
class Step:
    move: str  # UCI
    shapes: tuple[Shape, ...] = ()


@dataclass(frozen=True)
class Level:
    id: int  # 1-based within the stage, as in the URL
    goal: str
    fen: str
    color: chess.Color  # the side the learner plays
    stars: tuple[str, ...] = ()
    shapes: tuple[Shape, ...] = ()
    scenario: tuple[Step, ...] = ()


@dataclass(frozen=True)
class Stage:
    id: int  # numbered across every category, as in the URL: /learn#/<id>
    key: str
    title: str
    subtitle: str
    intro: str
    complete: str
    levels: tuple[Level, ...]


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    stages: tuple[Stage, ...]


def _unescape(m: re.Match[str]) -> str:
    code = m.group(1)
    if len(code) > 1:
        return chr(int(code[1:], 16))
    return {"n": "\n", "t": "\t", "r": "\r"}.get(code, code)


def parse_strings(script: str) -> dict[str, str]:
    """The strings file's plain entries, ``i['key']="text"``, by key."""
    return {k: _ESCAPE.sub(_unescape, v) for k, v in _STRING.findall(script)}


def _prose(text: str) -> str:
    """Learn's text with its line breaks, but for those inside a sentence.

    Learn shows every line break. Some only wrap a sentence to fit its board
    ("Attack your opponent's king\\nin a way that cannot be defended!"), and
    the line after them goes on in lower case; those become a space. The rest
    start a sentence, a list item ("- Black cannot move") or a table row
    ("Queen = 9"), and are kept.
    """
    return re.sub(r"\n(?=[a-z])", " ", text).strip()


class _Module:
    """The module holding the stages, and what its names refer to."""

    def __init__(self, script: str, strings: dict[str, str]) -> None:
        self.script = script
        self.strings = strings
        arrow = _ARROW_HELPER.search(script)
        circle = _CIRCLE_HELPER.search(script)
        if arrow is None or circle is None:
            raise ScriptError("found no shape helpers in Learn's script")
        self.arrow = (arrow.group(1), arrow.group(4))
        self.circle = (circle.group(1), circle.group(4))
        self.stages: dict[str, tuple[Obj, int]] = {}
        for m in _STAGE.finditer(script):
            start = m.end(1) + 1  # the stage object's opening brace
            reader = Reader(script, start)
            obj = reader.entries(KEEP)
            self.stages[m.group(1)] = (obj, start)
            # The minifier binds each stage again under the name the
            # categories use: ``he={key:"bishop",...},q=he``.
            alias = re.compile(rf",([\w$]+)={re.escape(m.group(1))}(?=[,;)])")
            if a := alias.match(script, reader.pos):
                self.stages[a.group(1)] = (obj, start)

    def text(self, value: Any, what: str) -> str:
        if isinstance(value, str):
            return _prose(value)
        if isinstance(value, Name) and value.path[:2] == ("i18n", "learn"):
            key = value.path[2] if len(value.path) == 3 else ""
            if key not in self.strings:
                raise ScriptError(f"Learn has no English string {key!r} for {what}")
            return _prose(self.strings[key])
        raise ScriptError(f"Learn's {what} is not a string")

    def categories(self) -> list[Category]:
        start = self.script.find(MARKER)
        if start < 0:
            raise ScriptError("found no Learn categories in the script")
        listed = Reader(self.script, start).value(KEEP)
        categories, number = [], 0
        for item in listed:
            if not isinstance(item, Obj):
                raise ScriptError("a Learn category is not an object")
            fields = item.fields()
            stages = []
            for ref in fields.get("stages", []):
                if not isinstance(ref, Name) or ref.path[0] not in self.stages:
                    raise ScriptError(f"Learn lists a stage it does not define: {ref}")
                number += 1
                stages.append(self._stage(number, *self.stages[ref.path[0]]))
            if not stages:
                raise ScriptError("a Learn category lists no stages")
            categories.append(
                Category(
                    key=_string(fields.get("key"), "a category's key"),
                    name=self.text(fields.get("name"), "a category's name"),
                    stages=tuple(stages),
                )
            )
        if not categories:
            raise ScriptError("found no Learn categories in the script")
        return categories

    def _stage(self, number: int, obj: Obj, start: int) -> Stage:
        fields = obj.fields()
        key = _string(fields.get("key"), "a stage's key")
        what = f"stage {key!r}"
        levels = tuple(
            self._level(index, props, what)
            for index, props in enumerate(self._levels(fields.get("levels"), start))
        )
        if not levels:
            raise ScriptError(f"Learn {what} has no levels")
        return Stage(
            id=number,
            key=key,
            title=self.text(fields.get("title"), f"{what}'s title"),
            subtitle=self.text(fields.get("subtitle"), f"{what}'s subtitle"),
            intro=self.text(fields.get("intro"), f"{what}'s introduction"),
            complete=self.text(fields.get("complete"), f"{what}'s ending"),
            levels=levels,
        )

    def _levels(self, value: Any, start: int) -> list[dict[str, Any]]:
        """Each level's properties, as the stage's ``.map(...)`` builds them.

        ``.map(t)`` takes each level as written; ``.map((e,a)=>t({...d,...e,
        k:v},a))`` merges in order: defaults ``d`` (an object, or a function
        returning one), then the level ``e``, then any fixed key.
        """
        literals, fn = (
            (value.array, value.fn) if isinstance(value, Mapped) else (value, None)
        )
        if not isinstance(literals, list):
            raise ScriptError("a Learn stage's levels are not a list")
        built = []
        for literal in literals:
            if not isinstance(literal, Obj) or literal.spreads:
                raise ScriptError("a Learn level is not a plain object")
            level = literal.fields()
            if fn is None or isinstance(fn, Name):
                built.append(level)
                continue
            if not (
                isinstance(fn, ArrowFn)
                and fn.params
                and isinstance(fn.body, Call)
                and fn.body.args
                and isinstance(fn.body.args[0], Obj)
            ):
                raise ScriptError("a Learn stage builds its levels in an unknown way")
            props: dict[str, Any] = {}
            for entry in fn.body.args[0].entries:
                if not isinstance(entry, Spread):
                    props[entry[0]] = entry[1]
                elif entry.value == Name((fn.params[0],)):
                    props.update(level)
                else:
                    props.update(self._defaults(entry.value, start))
            built.append(props)
        return built

    def _defaults(self, value: Any, before: int) -> dict[str, Any]:
        """The object a level's defaults name, from its nearest definition."""
        called = isinstance(value, Call) and not value.args
        name = value.callee if isinstance(value, Call) else value
        if not (called or isinstance(value, Name)) or not isinstance(name, Name):
            raise ScriptError(f"a Learn level spreads an unknown value: {value}")
        pattern = re.compile(rf"(?<![\w$.]){re.escape(name.path[0])}=(?=\{{|\(\)=>\()")
        found = [m for m in pattern.finditer(self.script, 0, before)]
        if not found:
            raise ScriptError(
                f"Learn's level defaults {name.path[0]!r} are not defined"
            )
        defined = Reader(self.script, found[-1].end()).value(KEEP)
        if isinstance(defined, ArrowFn):
            if not called:
                raise ScriptError("Learn spreads a function as level defaults")
            defined = defined.body
        if not isinstance(defined, Obj):
            raise ScriptError("Learn's level defaults are not an object")
        return defined.fields()

    def _level(self, index: int, props: dict[str, Any], what: str) -> Level:
        what = f"{what} level {index + 1}"
        fen = _string(props.get("fen"), f"{what}'s position")
        if len(fen.split()) == 4:
            fen += " 0 1"
        try:
            board = chess.Board(fen)
        except ValueError:
            raise ScriptError(f"Learn {what} has an unreadable position") from None
        color = props.get("color")
        if color not in (None, "white", "black"):
            raise ScriptError(f"Learn {what} names an unknown side")
        stars = props.get("apples", "")
        if isinstance(stars, str):
            stars = stars.split()
        if not (isinstance(stars, list) and all(_is_square(s) for s in stars)):
            raise ScriptError(f"Learn {what} has unreadable stars")
        return Level(
            id=index + 1,
            goal=self.text(props.get("goal"), f"{what}'s goal"),
            fen=fen,
            color=board.turn if color is None else color == "white",
            stars=tuple(stars),
            shapes=tuple(self._shape(s, what) for s in _list(props.get("shapes"))),
            scenario=tuple(self._step(s, what) for s in _list(props.get("scenario"))),
        )

    def _shape(self, value: Any, what: str) -> Shape:
        if isinstance(value, Call) and len(value.callee.path) == 1:
            helper = value.callee.path[0]
            args = list(value.args)
            if helper in (self.arrow[0], self.circle[0]) and 1 <= len(args) <= 2:
                arrow = helper == self.arrow[0]
                default = self.arrow[1] if arrow else self.circle[1]
                where, brush = args[0], args[1] if len(args) == 2 else default
                if (
                    isinstance(where, str)
                    and isinstance(brush, str)
                    and brush in _BRUSHES
                    and _is_square(where[:2])
                    and (not arrow or _is_square(where[2:4]))
                ):
                    return Shape(where[:2], where[2:4] if arrow else None, brush)
        raise ScriptError(f"Learn {what} has an unreadable shape: {value}")

    def _step(self, value: Any, what: str) -> Step:
        if isinstance(value, str):
            return Step(value)
        if isinstance(value, Obj):
            fields = value.fields()
            move = fields.get("move")
            if isinstance(move, str):
                shapes = tuple(
                    self._shape(s, what) for s in _list(fields.get("shapes"))
                )
                return Step(move, shapes)
        raise ScriptError(f"Learn {what} scripts an unreadable move: {value}")


def _string(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScriptError(f"Learn's {what} is not a string")
    return value


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScriptError(f"expected a list in Learn's script, got {value}")
    return value


def _is_square(text: Any) -> bool:
    return isinstance(text, str) and text in chess.SQUARE_NAMES


def read_learn(script: str, strings: dict[str, str]) -> list[Category]:
    """Every category of Learn, its stages and their levels, in order."""
    return _Module(script, strings).categories()


async def fetch_learn(client: Client) -> list[Category]:
    """Find Learn's stage module and strings through the manifest, and read them."""
    manifest = await client.json(MANIFEST_URL)
    modules = manifest.get("js") if isinstance(manifest, dict) else None
    entry = modules.get("learn") if isinstance(modules, dict) else None
    strings = modules.get(STRINGS) if isinstance(modules, dict) else None
    if not (isinstance(entry, dict) and isinstance(strings, str)):
        raise FetchError("Lichess's asset manifest names no Learn module")
    names = [f"learn.{entry.get('hash')}.js", *entry.get("imports", [])]
    for name in names:
        script = await client.text(f"{ASSETS}/{name}")
        if MARKER in script:
            break
    else:
        raise FetchError("no module of the Learn page holds its stages")
    english = parse_strings(await client.text(f"{ASSETS}/{STRINGS}.{strings}.js"))
    return read_learn(script, english)


def _draw(node: MoveNode, shapes: tuple[Shape, ...]) -> None:
    for shape in shapes:
        color = _BRUSHES[shape.brush]
        if shape.dest is None:
            node.highlights.append(Highlight(shape.orig, color))
        else:
            node.arrows.append(Arrow(shape.orig, shape.dest, color))


def level_game(stage: Stage, level: Level, *, first: bool, last: bool) -> GameItem:
    """One level as a game: position, goal, stars, hints and scripted moves."""
    root = MoveNode(san=None)
    _draw(root, level.shapes)
    root.highlights.extend(Highlight(square, STAR) for square in level.stars)
    board = chess.Board(level.fen)
    node = root
    for step in level.scenario:
        try:
            move = chess.Move.from_uci(step.move)
        except ValueError:
            move = None
        if move is None or move not in board.legal_moves:
            raise FetchError(
                f"Learn {stage.key!r} level {level.id} scripts {step.move!r}, "
                f"which cannot be played in {board.fen()}"
            )
        child = MoveNode(san=board.san(move))
        _draw(child, step.shapes)
        node.children.append(child)
        node = child
        board.push(move)
    comment = [stage.subtitle, stage.intro] if first else []
    comment.append(level.goal)
    if last:
        if node is root:
            comment.append(stage.complete)
        else:
            node.post_comment = stage.complete
    return GameItem(
        headers={
            "Event": stage.title,
            "Site": SITE,
            "Round": str(level.id),
        },
        starting_fen=level.fen,
        comment="\n\n".join(comment),
        root=root,
    )


def stage_pgn(collection: Collection, stage: Stage) -> str:
    count = len(stage.levels)
    return "\n\n".join(
        game_to_pgn(
            collection,
            level_game(stage, level, first=n == 0, last=n == count - 1),
        )
        for n, level in enumerate(stage.levels)
    )


_TARGET = re.compile(
    r"^https?://(?:www\.)?lichess\.org/learn/?(?:#/(?P<stage>\d+)(?:/\d+)?/?|#/?)?$"
)


def stage_from(target: str) -> int:
    """The stage a Learn URL points at (``/learn#/3``).

    The stage is in the fragment, so a query (a share link's ``?utm_...``) is
    dropped only up to it.
    """
    m = _TARGET.match(re.sub(r"\?[^#]*", "", target.strip(), count=1))
    if m is None:
        raise ValueError(f"could not read a lichess Learn stage from {target!r}")
    if m.group("stage") is None:
        raise WholeSection(
            f"{target!r} is all of Learn; give one stage's URL (lichess.org/learn#/3)"
        )
    return int(m.group("stage"))


def layout(categories: list[Category]) -> list[tuple[Stage, tuple[str, str]]]:
    """Every stage with its path: category directory, then stage, both numbered."""
    return [
        (stage, (f"{n:02d}-{category.key}", f"{m:02d}-{slugify(stage.title)}"))
        for n, category in enumerate(categories, start=1)
        for m, stage in enumerate(category.stages, start=1)
    ]


@dataclass
class _Context:
    collection: Collection
    stages: dict[str, Stage]


class LearnExtractor(Extractor[_Context]):
    """The stage of Learn a URL points at."""

    key = "lichess:learn"
    url_patterns = (re.compile(r"^https?://(?:www\.)?lichess\.org/learn(?:[/?#]|$)"),)

    def __init__(self, target: str) -> None:
        super().__init__()
        self.stage = stage_from(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        categories = await fetch_learn(Client(fetcher))
        placed = [(s, path) for s, path in layout(categories) if s.id == self.stage]
        if not placed:
            raise FetchError(f"lichess Learn has no stage {self.stage}")
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        refs = [ItemRef(id=stage.key, path=path) for stage, path in placed]
        stages = {stage.key: stage for stage, _ in placed}
        return collection, refs, _Context(collection, stages)

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        return GameItem(pgn=stage_pgn(context.collection, context.stages[ref.id]))
