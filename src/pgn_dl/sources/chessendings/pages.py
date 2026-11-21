"""What chessendings's pages hold, read out of their trees.

Each reader finds its data by the shape of the props carrying it (see
``flight.props_with``) and refuses a page without them: the server answers 200
for a page it renders differently (a Pro offer, a signed-out panel), so the
props' absence is the only sign of either.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...core.fetch import FetchError
from . import flight


class ProRequired(FetchError):
    """The item is Pro content the account downloading cannot open."""


class SignedOut(FetchError):
    """A request carrying the saved session was answered as a guest's."""


def check_signed_in(tree: Any) -> None:
    """Refuse a page rendered for a guest, when the session was sent.

    The navigation is rendered with the signed-in account, or ``None`` for a
    guest. A listing rendered for a guest marks every Pro item locked, so
    without this a refused session would pass for an account without Pro.
    """
    nav = flight.props_with(tree, "account")
    if nav is None or not nav["account"]:
        raise SignedOut(
            "chessendings answered as signed out rather than as your account; "
            "the saved session was not accepted"
        )


def listing_rows(tree: Any, *keys: str, subject: str) -> list[Mapping[str, Any]]:
    """The ``rows`` a listing page renders its list from, each carrying ``keys``."""
    listing = flight.find(
        tree,
        lambda node: (
            isinstance(node, dict)
            and isinstance(node.get("rows"), list)
            and bool(node["rows"])
            and all(isinstance(r, dict) and keys[0] in r for r in node["rows"])
        ),
    )
    if listing is None:
        raise FetchError(
            f"found no {subject} listing on chessendings; the site has changed"
        )
    rows = listing["rows"]
    for row in rows:
        missing = [key for key in keys if key not in row]
        if missing:
            raise FetchError(f"a chessendings {subject} row has no {missing[0]}")
    return rows


def drill_props(tree: Any, subject: str) -> Mapping[str, Any]:
    """A drill page's props.

    Only asked of a page the listing showed as open to the account, so a page
    without a drill is one this module no longer recognizes.
    """
    props = flight.props_with(tree, "initialFen", "initialHeroColor")
    if props is None:
        raise FetchError(f"{subject} has no drill on its page; the site has changed")
    return props
