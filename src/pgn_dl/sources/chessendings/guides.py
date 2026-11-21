"""chessendings endgame guides.

The guides (/guides) are articles under a handful of chapters (checkmates,
rook endgames, ... rules and concepts): prose around diagrams, most of which
step through a line (see ``article.py``). They are laid out by chapter in the
index's order (see ``section.py``), and are public, so no session is sent.

Each guide is one file with a game per line. A game's comment is the heading
and paragraphs of the section the line ends, then the diagram's caption; the
first game also opens with the guide's opening paragraphs. A section without a
line of its own (a definition, the FAQ) follows the game before it, after its
moves. Each frame's drawings go on the position it shows. A guide without any
line is one game of its text, set at the position of the lesson it embeds, or
at the standard position when it embeds none. A static diagram is a picture
with no position to read, so only its caption is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef, MoveNode
from ...core.paths import slugify
from ...core.pgn import game_to_pgn
from . import flight
from .article import Article, Heading, Lesson, Paragraph, Replay, read_article, text
from .client import SITE, Client
from .drill import add_drawings, paragraphs, players, read_drawings
from .section import Listed, layout
from .urls import GUIDE_HREF, slug_from, under

#: The section's name on the site.
COLLECTION_NAME = "Endgame guides"


def parse_index(tree: Any) -> list[Listed]:
    """Every guide the index lists, in order, each in the chapter it is under.

    A link outside any chapter (a featured guide above them, say) is not a
    place in the index, so it numbers nothing.
    """
    chapter, guides = "", {}
    for node in flight.walk(tree):
        if flight.is_element(node) and node[1] == "h2":
            chapter = text(node)
        elif (
            chapter
            and isinstance(node, dict)
            and isinstance(node.get("href"), str)
            and (m := GUIDE_HREF.fullmatch(node["href"])) is not None
        ):
            guides.setdefault(m.group(1), chapter)
    if not guides:
        raise FetchError("found no chessendings guides; the site has changed")
    return [
        Listed(slug=slug, group=chapter, order=n)
        for n, (slug, chapter) in enumerate(guides.items())
    ]


@dataclass
class _Game:
    """One game of a guide while its text is gathered."""

    replay: Replay | None
    leading: list[str] = field(default_factory=list)
    trailing: list[str] = field(default_factory=list)


def _gather(article: Article) -> tuple[list[_Game], Lesson | None]:
    """The guide's games with their text, and the lesson it embeds, if any.

    A section's heading and paragraphs wait for the section's line. The next
    heading, or the end of the article, means none is coming, and they follow
    the game before them; before any game they lead the first. A heading with
    nothing under it (the one over the embedded lesson) is dropped.
    """
    games: list[_Game] = []
    waiting: list[str] = list(article.lede)  # for the first game, until there is one
    heading: list[str] = []  # the open section's heading, if it has one
    section: list[str] = []  # its paragraphs not yet placed
    lesson: Lesson | None = None

    def settle() -> None:
        if section:
            (games[-1].trailing if games else waiting).extend(heading + section)
        heading.clear()
        section.clear()

    for block in article.blocks:
        if isinstance(block, Heading):
            settle()
            heading.append(block.text)
        elif isinstance(block, Paragraph):
            section.append(block.text)
        elif isinstance(block, Lesson):
            lesson = lesson or block
        else:
            captions = [block.caption] if block.caption else []
            games.append(_Game(block, [*waiting, *heading, *section, *captions]))
            waiting = []
            heading.clear()
            section.clear()
    settle()
    if not games:
        games.append(_Game(None, waiting))
    return games, lesson


def _line(replay: Replay, subject: str) -> MoveNode:
    """A diagram's line as a move tree, each frame's drawings on its position."""
    try:
        board = chess.Board(replay.fen)
    except ValueError:
        raise FetchError(f"{subject} has an unreadable position") from None
    root = MoveNode(san=None)
    positions = [root]  # frame k is the position after k moves
    for san in replay.moves:
        try:
            move = board.parse_san(san)
        except ValueError:
            raise FetchError(
                f"{subject} plays {san!r}, illegal in {board.fen()}"
            ) from None
        child = MoveNode(san=board.san(move))
        board.push(move)
        positions[-1].children.append(child)
        positions.append(child)
    for node, frame in zip(positions, replay.annotations):
        if frame:
            add_drawings(node, read_drawings(frame, subject))
    return root


def guide_games(article: Article, *, slug: str, chapter: str) -> list[GameItem]:
    """A guide's games: one per line, its text in their comments."""
    games, lesson = _gather(article)
    items = []
    for number, game in enumerate(games, start=1):
        subject = f"chessendings guide {slug} diagram {number}"
        if game.replay is not None:
            fen, root = game.replay.fen, _line(game.replay, subject)
        else:
            fen = lesson.fen if lesson is not None else chess.STARTING_FEN
            root = MoveNode(san=None)
        last = root
        while last.children:
            last = last.children[0]
        comment = paragraphs(*game.leading)
        if last is root:
            comment = paragraphs(comment, *game.trailing)
        else:
            last.post_comment = paragraphs(last.post_comment, *game.trailing)
        if game.replay is not None and game.replay.pov:
            color = game.replay.pov == "w"
        else:
            color = chess.Board(fen).turn
        headers = {
            "Event": article.title,
            "Site": SITE,
            "Round": str(number),
            **players(article.title, color),
            "ChessendingsSlug": slug,
            "ChessendingsTheme": chapter,
        }
        if lesson is not None:
            headers["ChessendingsLesson"] = lesson.slug
        items.append(
            GameItem(headers=headers, starting_fen=fen, comment=comment, root=root)
        )
    return items


@dataclass
class _Context:
    client: Client
    collection: Collection
    chapters: dict[str, str]  # guide slug -> chapter


class GuidesExtractor(Extractor[_Context]):
    """Every guide, or one."""

    key = "chessendings:guides"
    url_patterns = under("guides")

    def __init__(self, target: str) -> None:
        super().__init__()
        self.slug = slug_from(target, "guides", noun="guide")

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        client = Client(fetcher, signed_in=False)
        placed = layout(parse_index(await client.page("/guides")), width=2)
        if self.slug is not None:
            placed = [(x, path) for x, path in placed if x.slug == self.slug]
            if not placed:
                raise FetchError(f"chessendings has no guide {self.slug!r}")
        refs = [ItemRef(id=x.slug, path=path) for x, path in placed]
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        chapters = {x.slug: x.group for x, _ in placed}
        return collection, refs, _Context(client, collection, chapters)

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        tree = await context.client.page(f"/guides/{ref.id}")
        article = read_article(tree, f"chessendings guide {ref.id}")
        games = guide_games(article, slug=ref.id, chapter=context.chapters[ref.id])
        return GameItem(
            pgn="\n\n".join(game_to_pgn(context.collection, g) for g in games)
        )
