"""chessendings Theory lessons.

Theory (/train) is 318 lessons in ten families (checkmates, king and pawn, ...
queen against rook), each a position of at most seven pieces with the method
played through move by move (see ``drill.py``), laid out by family (see
``section.py``). The free lessons need no account; with a saved session the
listing is the account's, so an account with Pro gets every lesson.

Every lesson also has a public page (/endgames/<slug>) whose "The theory"
section holds an essay on the ending that the lesson page lacks. The essay is
the paragraphs after the section's replay, which is the lesson's mainline
again, and it joins the game comment after the introduction; the guide the
page points to is named in a header. If the page has no essay, or this module
can no longer read it, the lesson is written without one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...core.fetch import FetchError
from . import flight
from .article import Paragraph, Replay, read_article
from .client import Client
from .section import Listed, SectionExtractor
from .urls import GUIDE_HREF, slug_from, under


def essay_of(tree: Any, subject: str) -> str:
    """The essay on a lesson's public page, or "" when it has none to read."""
    try:
        blocks = read_article(tree, subject).blocks
    except FetchError:
        return ""
    start = next((n for n, b in enumerate(blocks) if isinstance(b, Replay)), None)
    if start is None:
        return ""
    return "\n\n".join(b.text for b in blocks[start + 1 :] if isinstance(b, Paragraph))


def guide_of(tree: Any) -> str | None:
    """The guide a lesson's public page points to, if any."""
    for node in flight.walk(tree):
        if (
            isinstance(node, dict)
            and isinstance(node.get("href"), str)
            and (m := GUIDE_HREF.fullmatch(node["href"])) is not None
        ):
            return m.group(1)
    return None


class TheoryExtractor(SectionExtractor):
    """Every Theory lesson the account can open, or one."""

    key = "chessendings:theory"
    url_patterns = under("train", "endgames")
    title = "Theory"
    listing_path = "/train"
    noun = "lesson"
    order_key = "familyOrder"
    group_key = "motifLabel"

    def __init__(self, target: str) -> None:
        super().__init__()
        self.slug = slug_from(target, "train", "endgames", noun="Theory lesson")

    def describe(self, row: Mapping[str, Any]) -> tuple[dict[str, str], str]:
        band, elo = row.get("bandLabel"), row.get("elo")
        if isinstance(band, str) and isinstance(elo, str) and band and elo:
            return {"ChessendingsLevel": f"{band} ({elo})"}, ""
        return {}, ""

    async def extras(self, client: Client, drill: Listed) -> tuple[str, dict[str, str]]:
        try:
            tree = await client.page(f"/endgames/{drill.slug}")
        except FetchError as exc:
            if exc.status == 404:
                return "", {}
            raise
        essay = essay_of(tree, f"chessendings lesson {drill.slug}'s public page")
        guide = guide_of(tree)
        return essay, {"ChessendingsGuide": guide} if guide else {}
