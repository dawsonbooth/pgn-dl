"""chessendings Strategy positions.

Strategy (/practice) is 236 positions from real games, eight to twelve pieces
each, under seven ideas ("Walk the king in", "Open a second front", ...),
laid out by idea (see ``section.py``). Past the tablebase there is no line to
replay, so a position's game is its board, the coach's plan and the game it
came from, without moves (see ``drill.py``).

The listing needs an account. The first five positions of every idea are free
and the rest need Pro.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .section import SectionExtractor
from .urls import slug_from, under


class StrategyExtractor(SectionExtractor):
    """One Strategy position."""

    key = "chessendings:strategy"
    url_patterns = under("practice")
    title = "Strategy"
    listing_path = "/practice"
    noun = "position"
    order_key = "order"
    group_key = "themeLabel"
    members_only = True

    def __init__(self, target: str) -> None:
        super().__init__()
        self.slug = slug_from(
            target,
            "practice",
            noun=f"{self.title} {self.noun}",
            whole=f"all of {self.title}",
        )

    def describe(self, row: Mapping[str, Any]) -> tuple[dict[str, str], str]:
        origin = row.get("origin")
        if not isinstance(origin, str) or not origin.strip():
            return {}, ""
        return {"ChessendingsOrigin": origin.strip()}, f"From {origin.strip()}."
