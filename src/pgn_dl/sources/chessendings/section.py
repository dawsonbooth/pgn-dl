"""The site's listings, and the two sections made of drill pages.

A listing page names every item of a section in order and in groups (Theory's
families, Strategy's ideas, the guides' chapters, the challenges' themes).
Groups are numbered directories in the listing's order and items are numbered
within their group, both from the whole listing, so a number depends neither on
what the account may open nor on which item a run asks for. Files are named by
the site's own slug.

Theory and Strategy also share how an item is fetched: each is a drill page of
its own (see ``drill.py``). Their listings are rendered for whoever asks: an
item flagged ``pro`` is one this viewer cannot open (the flag drives the PRO
badge, which a guest sees on every Pro item and a Pro account on none). Such an
item fails without a request. An item whose page comes back rendered for a
guest, although the session was sent, fails with ``SignedOut``: the session no
longer counts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from . import auth
from .client import Client
from .drill import drill_game, parse_drill
from .pages import ProRequired, check_signed_in, drill_props, listing_rows
from .urls import SLUG


@dataclass(frozen=True)
class Listed:
    """One item as a listing shows it."""

    slug: str
    group: str
    order: int
    locked: bool = False  # Pro content this viewer cannot open
    headers: Mapping[str, str] = field(default_factory=dict)
    preface: str = ""  # said in the game comment before the introduction


def layout(
    items: list[Listed], *, width: int = 3
) -> list[tuple[Listed, tuple[str, str]]]:
    """Each item in the listing's order with its path: group directory, number."""
    items = sorted(items, key=lambda item: item.order)
    groups = list(dict.fromkeys(item.group for item in items))
    directories = {
        group: f"{n:02d}-{slugify(group, 'group')}"
        for n, group in enumerate(groups, start=1)
    }
    counts = dict.fromkeys(groups, 0)
    placed = []
    for item in items:
        counts[item.group] += 1
        name = f"{counts[item.group]:0{width}d}-{slugify(item.slug)}"
        placed.append((item, (directories[item.group], name)))
    return placed


def _without_pro(signed_in: bool) -> str:
    """Why a Pro item was not downloaded, and what would change that."""
    if signed_in:
        return "this account does not have Pro"
    return "run: pgn-dl login chessendings, if your account has Pro"


@dataclass
class _Context:
    client: Client
    listed: dict[str, Listed]


class SectionExtractor(Extractor[_Context]):
    """One drill of a section, by its slug."""

    #: The section's title on the site, which names its collection.
    title: ClassVar[str] = ""
    #: Where the section lists its drills; each drill's page is under it.
    listing_path: ClassVar[str] = ""
    #: What the section calls one drill, for messages.
    noun: ClassVar[str] = ""
    #: The listing row's keys for a drill's place and its group's name.
    order_key: ClassVar[str] = ""
    group_key: ClassVar[str] = ""
    #: Whether the listing is for accounts only.
    members_only: ClassVar[bool] = False

    slug: str

    def describe(self, row: Mapping[str, Any]) -> tuple[dict[str, str], str]:
        """The headers a listing row adds to its drill's game, and a preface."""
        return {}, ""

    async def extras(self, client: Client, drill: Listed) -> tuple[str, dict[str, str]]:
        """An essay for the game comment and headers, from beyond the drill's page."""
        return "", {}

    def parse_listing(self, tree: Any) -> list[Listed]:
        """Every drill the listing names.

        A row whose slug, place or group cannot be read is refused: the slug
        names the file and the place and group number it, so a guess at any of
        them could move every file on a later run.
        """
        listed = []
        rows = listing_rows(
            tree, self.order_key, "slug", self.group_key, subject=self.title
        )
        for row in rows:
            slug, order, group = row["slug"], row[self.order_key], row[self.group_key]
            if not isinstance(slug, str) or not re.fullmatch(SLUG, slug):
                raise FetchError(
                    f"a chessendings {self.title} row has an unreadable slug"
                )
            if not isinstance(order, int) or isinstance(order, bool):
                raise FetchError(f"chessendings {self.title} row {slug} has no order")
            if not isinstance(group, str) or not group.strip():
                raise FetchError(f"chessendings {self.title} row {slug} has no group")
            headers, preface = self.describe(row)
            listed.append(
                Listed(
                    slug=slug,
                    group=group.strip(),
                    order=order,
                    locked=row.get("pro") is True,
                    headers={
                        "ChessendingsSlug": slug,
                        "ChessendingsTheme": group.strip(),
                        **headers,
                    },
                    preface=preface,
                )
            )
        return listed

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        # A members-only listing asks for the session even with none saved, so
        # a run without one ends in the credential store's own login hint.
        client = Client(
            fetcher, signed_in=self.members_only or auth.has_saved_session()
        )
        tree = await client.page(self.listing_path)
        if client.signed_in:
            check_signed_in(tree)
        listed = self.parse_listing(tree)
        placed = [(x, path) for x, path in layout(listed) if x.slug == self.slug]
        if not placed:
            raise FetchError(
                f"chessendings has no {self.title} {self.noun} {self.slug!r}"
            )
        refs = [ItemRef(id=x.slug, path=path) for x, path in placed]
        collection = self.collection(slug=slugify(self.title), name=self.title)
        return collection, refs, _Context(client, {x.slug: x for x, _ in placed})

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        drill = context.listed[ref.id]
        subject = f"chessendings {self.noun} {drill.slug}"
        if drill.locked:
            raise ProRequired(
                f"{subject} is Pro content; {_without_pro(context.client.signed_in)}"
            )
        tree = await context.client.page(f"{self.listing_path}/{drill.slug}")
        if context.client.signed_in:
            check_signed_in(tree)
        game = parse_drill(drill_props(tree, subject), subject)
        essay, headers = await self.extras(context.client, drill)
        return drill_game(
            game,
            subject=subject,
            headers={**drill.headers, **headers},
            preface=drill.preface,
            essay=essay,
        )
