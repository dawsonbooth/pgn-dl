"""chessendings endgame challenges.

The challenges (/challenges) are categories of five positions under six themes
(checkmates, pawns, minor pieces, rooks, queens, imbalances), each a position
and a condition to meet against perfect defense: mate in N, hold N moves, or
win. Nothing is written down to replay, so each position is a game without
moves, and each category is one file of its positions in the site's order,
easiest first, laid out by theme (see ``section.py``).

Racing the positions needs an account, but every category's Positions tab
shows them to anyone, so no session is sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from ...core.pgn import game_to_pgn
from . import flight
from .client import SITE, Client
from .drill import players, task
from .section import Listed, layout
from .urls import HOST, SLUG, under

#: The section's title on the site.
COLLECTION_NAME = "Endgame challenges"

#: The challenges page, a theme page, a category page, or a bare category slug.
_SCOPE_RE = re.compile(
    rf"^(?:{HOST}/challenges(?:/t/(?P<theme>{SLUG})|/(?!t(?:/|$))(?P<category>{SLUG}))?/?"
    rf"|(?P<bare>{SLUG}))$",
    re.IGNORECASE,
)
_THEME_HREF = re.compile(rf"/challenges/t/({SLUG})")
_CATEGORY_HREF = re.compile(rf"/challenges/(?!t(?:/|$))({SLUG})")
#: A breadcrumb entry's React key, ``<depth>:<label>``.
_CRUMB = re.compile(r"(\d+):(.+)")
_CAPTION = re.compile(r"Position (\d+) · (.+)")
#: The conditions a caption states, with the goal each sets.
_CONDITIONS = (
    (re.compile(r"Mate in (\d+)"), "win", "mate in {}"),
    (re.compile(r"Hold (\d+) moves"), "draw", "hold {} moves"),
    (re.compile(r"Win"), "win", ""),
)


@dataclass(frozen=True)
class Scope:
    """Which challenges a target names: every one, a theme's, or a category."""

    theme: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class Challenge:
    number: int
    fen: str
    color: chess.Color  # the side to move, which plays it
    goal: str  # "win" or "draw"
    condition: str  # "mate in 6", "hold 8 moves", or "" for a bare win


def scope_from(target: str) -> Scope:
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _SCOPE_RE.match(cleaned)
    if m is None:
        raise ValueError(f"could not read chessendings challenges from {target!r}")
    theme, category = m.group("theme"), m.group("category") or m.group("bare")
    return Scope(
        theme=theme.lower() if theme else None,
        category=category.lower() if category else None,
    )


def _hrefs(tree: Any, pattern: re.Pattern[str]) -> list[str]:
    """The slugs of the links matching ``pattern``, in page order, once each."""
    found = (
        m.group(1)
        for node in flight.walk(tree)
        if isinstance(node, dict)
        and isinstance(node.get("href"), str)
        and (m := pattern.fullmatch(node["href"])) is not None
    )
    return list(dict.fromkeys(found))


def _crumbs(tree: Any, category: str) -> tuple[str, str]:
    """The theme and category names in a category page's breadcrumb."""
    crumbs: dict[int, str] = {}
    for node in flight.walk(tree):
        if (
            flight.is_element(node)
            and node[1] == "span"
            and isinstance(node[2], str)
            and (m := _CRUMB.fullmatch(node[2])) is not None
        ):
            crumbs.setdefault(int(m.group(1)), m.group(2))
    if 1 not in crumbs or 2 not in crumbs:
        raise FetchError(f"chessendings challenge {category!r} has no breadcrumb")
    return crumbs[1], crumbs[2]


def _condition(text: str, category: str) -> tuple[str, str]:
    for pattern, goal, wording in _CONDITIONS:
        if (m := pattern.fullmatch(text)) is not None:
            return goal, wording.format(*m.groups())
    raise FetchError(f"chessendings challenge {category!r} sets {text!r}")


def parse_challenges(tree: Any, category: str) -> list[Challenge]:
    """A category page's positions, in the site's order.

    Read from the Positions tab's list, one item per position, each a board
    preview captioned ``Position <n> · <condition>``. A position this module
    cannot read fails the category: a file written a position short would be
    skipped as done by every later run.
    """
    challenges = []
    for node in flight.walk(tree):
        if not (flight.is_element(node) and node[1] == "li"):
            continue
        child = node[3].get("children")
        if not flight.is_element(child) or "fen" not in child[3]:
            continue
        fen, caption = child[3].get("fen"), child[3].get("caption")
        m = _CAPTION.fullmatch(caption) if isinstance(caption, str) else None
        if not isinstance(fen, str) or m is None:
            raise FetchError(f"a chessendings {category} challenge is unreadable")
        try:
            color = chess.Board(fen).turn
        except ValueError:
            raise FetchError(
                f"chessendings {category} challenge {m.group(1)} has an "
                "unreadable position"
            ) from None
        goal, condition = _condition(m.group(2).strip(), category)
        challenges.append(Challenge(int(m.group(1)), fen, color, goal, condition))
    if not challenges:
        raise FetchError(f"found no positions for chessendings challenge {category!r}")
    return challenges


def challenge_game(
    challenge: Challenge, *, title: str, theme: str, category: str
) -> GameItem:
    """One position as a game: its board and its condition, no moves."""
    comment = task(challenge.color, challenge.goal)
    if challenge.condition:
        comment += f": {challenge.condition}"
    return GameItem(
        headers={
            "Event": title,
            "Site": SITE,
            "Round": str(challenge.number),
            **players(title, challenge.color),
            "ChessendingsSlug": category,
            "ChessendingsTheme": theme,
        },
        starting_fen=challenge.fen,
        comment=f"{comment}.",
    )


@dataclass
class _Context:
    client: Client
    collection: Collection


class ChallengesExtractor(Extractor[_Context]):
    """Every challenge category, a theme's, or one."""

    key = "chessendings:challenges"
    url_patterns = under("challenges")

    def __init__(self, target: str) -> None:
        super().__init__()
        self.scope = scope_from(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        client = Client(fetcher, signed_in=False)
        themes = _hrefs(await client.page("/challenges"), _THEME_HREF)
        if not themes:
            raise FetchError(
                "found no chessendings challenge themes; the site has changed"
            )
        if self.scope.theme is not None and self.scope.theme not in themes:
            raise FetchError(
                f"chessendings has no challenge theme {self.scope.theme!r}"
            )
        categories = []
        for theme in themes:
            page = await client.page(f"/challenges/t/{theme}")
            for category in _hrefs(page, _CATEGORY_HREF):
                categories.append(
                    Listed(slug=category, group=theme, order=len(categories))
                )
        placed = [
            (x, path)
            for x, path in layout(categories, width=2)
            if self.scope.theme in (None, x.group)
            and self.scope.category in (None, x.slug)
        ]
        if self.scope.category is not None and not placed:
            raise FetchError(f"chessendings has no challenge {self.scope.category!r}")
        refs = [ItemRef(id=x.slug, path=path) for x, path in placed]
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        return collection, refs, _Context(client, collection)

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        tree = await context.client.page(f"/challenges/{ref.id}?tab=practice")
        theme, title = _crumbs(tree, ref.id)
        games = [
            game_to_pgn(
                context.collection,
                challenge_game(challenge, title=title, theme=theme, category=ref.id),
            )
            for challenge in parse_challenges(tree, ref.id)
        ]
        return GameItem(pgn="\n\n".join(games))
