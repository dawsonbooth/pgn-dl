"""The Chessable courses extractor.

``discover`` reads the course outline (getCourse) and lays out one item per
variation, Course -> Chapter -> Variation, exactly as chess.com courses do.
``fetch`` pulls one variation (getGame) and rebuilds its move tree.
"""

from __future__ import annotations

import re

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from . import auth
from .client import GameClient
from .parser import parse_course, parse_game

SITE = "chessable.com"

# A course id in any Chessable URL - ``/course/<id>`` or ``/learn/<id>`` - or a
# bare number. Anchored to those two path shapes so a blog or profile URL under
# the same host is declined rather than mis-parsed.
_URL_ID = re.compile(r"/(?:course|learn)/(\d+)")
_BARE_ID = re.compile(r"^\d+$")


def course_id_from(target: str) -> str:
    """The numeric course id in a Chessable URL, or a bare id."""
    cleaned = target.strip()
    if _BARE_ID.match(cleaned):
        return cleaned
    m = _URL_ID.search(cleaned)
    if m is None:
        raise ValueError(f"could not find a Chessable course id in {target!r}")
    return m.group(1)


class CoursesExtractor(Extractor):
    key = "chessable:courses"
    url_patterns = (re.compile(r"^https?://(www\.)?chessable\.com/"),)
    needs_browser = False

    def __init__(self, target: str) -> None:
        self.course_id = course_id_from(target)
        self._client: GameClient | None = None
        # Keyed by ItemRef.path (unique per item), not by oid: while an oid does
        # not repeat across chapters today, the path is what the Round belongs to.
        self._rounds: dict[tuple[str, ...], str] = {}

    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        creds = auth.load_credentials()
        self._client = GameClient(fetcher, jwt=creds["jwt"], uid=creds["uid"])
        outline = parse_course(await self._client.get_course(self.course_id))

        if not outline.owned:
            # getGame would then fail per variation; one clear stop beats a wall
            # of per-item failures. Not a re-login matter, but the runner may add
            # the hint, which is harmless: a stale session reads as not-owned too.
            raise FetchError(
                f"chessable course {self.course_id} is not owned by this account; "
                "add it to your Chessable library, or check the course id"
            )

        refs: list[ItemRef] = []
        for ci, chapter in enumerate(outline.chapters, start=1):
            chap = f"{ci:02d}-{slugify(chapter.title, 'chapter')}"
            for vi, var in enumerate(chapter.variations, start=1):
                name = slugify(var.name, "variation")
                ref = ItemRef(id=var.oid, path=(chap, f"{vi:03d}-{name}"))
                refs.append(ref)
                self._rounds[ref.path] = f"{ci}.{vi}"

        collection = self.collection(
            slug=f"{slugify(outline.name, 'course')}-{self.course_id}",
            name=outline.name,
            headers={"Event": outline.name, "Site": SITE},
        )
        return collection, refs

    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        # ``fetcher`` is unused: discover() built a client around the same one,
        # and the client carries the credential every request needs.
        if self._client is None:
            raise FetchError("chessable fetch called before discover")
        content = parse_game(await self._client.get_game(self.course_id, ref.id))
        headers = {"ChessableVariationId": content.oid or ref.id}
        if round_ := self._rounds.get(ref.path):
            headers["Round"] = round_
        if content.result != "*":
            headers["Result"] = content.result
        return GameItem(
            headers=headers,
            starting_fen=content.starting_fen or chess.STARTING_FEN,
            comment=content.description,
            root=content.root,
        )
