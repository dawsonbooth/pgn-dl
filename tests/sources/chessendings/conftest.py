"""Fakes for the chessendings tests.

Pages are invented in the live shape, never captured: half the curriculum is
paid content. ``stream`` is a miniature flight encoder, so a test can state a
page as the tree it renders and stay readable; the parser's own suite covers
the wire format's corners.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessendings import auth

ROOT = "https://chessendings.com"


def games(text: str) -> list[chess.pgn.Game]:
    """Every game in a PGN text, each re-read without errors."""
    buf, found = io.StringIO(text), []
    while (game := chess.pgn.read_game(buf)) is not None:
        assert not game.errors
        found.append(game)
    return found


SESSION = f"{auth.COOKIE_NAME}=STUB"


def stream(tree: object, **rows: object) -> str:
    """A flight stream whose page (row ``0``) is ``tree``.

    Row ``1`` is a client module, as every component reference points at
    one; ``rows`` adds JSON rows by id for a tree that references them.
    """
    lines = ['1:I[100,["/_next/static/chunks/a.js"],"Component"]']
    lines += [f"{row_id}:{json.dumps(value)}" for row_id, value in rows.items()]
    lines.append(f"0:{json.dumps(tree)}")
    return "\n".join(lines) + "\n"


def component(props: Mapping[str, Any], key: str | None = None) -> list:
    """A client component, the way the page renders its data into one."""
    return ["$", "$L1", key, dict(props)]


def element(tag: str, props: Mapping[str, Any], key: str | None = None) -> list:
    return ["$", tag, key, dict(props)]


def page(*children: object, account: object = None, locale: str = "en") -> list:
    """A page's tree, as the layout wraps every page.

    The locale provider names the language the page was rendered in, and the
    navigation names the account (``None`` for a guest).
    """
    navigation = component({"account": account})
    return component(
        {
            "locale": locale,
            "source": "preference",
            "children": [element("main", {"children": [navigation, *children]})],
        }
    )


ACCOUNT = {"id": "u-1", "email": "player@example.com"}


# Theory.


def theory_row(
    slug: str, family: str, order: int, *, pro: bool = False, **extra: Any
) -> dict[str, Any]:
    return {
        "slug": slug,
        "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
        "heroColor": "w",
        "motif": family.lower().split()[0],
        "motifLabel": family,
        "band": "class-c",
        "bandLabel": "Class C",
        "elo": "1400–1599",
        "title": slug.replace("-", " ").title(),
        "intro": "An intro.",
        "caption": "White to play and win",
        "status": "new",
        "pro": pro,
        "familyOrder": order,
        "levelOrder": order,
        **extra,
    }


#: Out of order, as the live listing is (it is sorted by level).
THEORY_ROWS = (
    theory_row("lucena-position", "Rook endgames", 3),
    theory_row("king-rook-checkmate", "Checkmates", 1),
    theory_row("philidor-position", "Rook endgames", 4, pro=True),
    theory_row("king-queen-checkmate", "Checkmates", 0),
    theory_row("opposition-basics", "King & pawn", 2),
)


def theory_listing(rows: tuple[dict[str, Any], ...] = THEORY_ROWS, **kw: Any) -> str:
    return stream(page(component({"rows": list(rows)}), **kw))


#: A rook ending, played from White's side: 1. Rd1+ Ke7 2. Rd4 is the method,
#: 1. Ra1 Rxa1 the stalemate trap.
LUCENA_FEN = "1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1"


def drill_props(**overrides: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "slug": "lucena-position",
        "lesson": [
            {
                "comment": "Never play Ra1?? It is stalemate.",
                "demo": {"moves": ["c1a1", "a2a1"]},
                "highlightSquares": ["c7", "a7"],
            },
            {
                "comment": "Rd1+, and his king steps away.",
                "move": "c1d1",
                "reply": "d8e7",
                "highlightSquares": ["c7"],
                "notTheFastestMate": True,
            },
            {
                "comment": "Rd4 builds the bridge.",
                "move": "d1d4",
                "reply": "e7e6",
                "zones": [{"from": "a4", "to": "b5"}],
                "arrows": [{"from": "d4", "to": "b4", "kind": "line"}],
            },
        ],
        "book": [],
        "title": "The Lucena Position",
        "intro": "Build a bridge.",
        "introHighlightSquares": None,
        "goal": "win",
        "proTier": False,
        "initialFen": LUCENA_FEN,
        "initialHeroColor": "w",
    }
    return props | overrides


def drill_page(props: Mapping[str, Any] | None = None, **kw: Any) -> str:
    return stream(page(component(drill_props() if props is None else props), **kw))


# Strategy.


def strategy_row(
    slug: str, idea: str, order: int, *, pro: bool = False, origin: str | None = None
) -> dict[str, Any]:
    return {
        "slug": slug,
        "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
        "heroColor": "w",
        "theme": idea.lower().replace(" ", "-"),
        "themeLabel": idea,
        "order": order,
        "goal": "win",
        "title": slug.replace("-", " ").capitalize(),
        "brief": "A plan.",
        "origin": origin,
        "caption": "White to play and win",
        "pro": pro,
    }


STRATEGY_ROWS = (
    strategy_row("two-weaknesses-win", "Two weaknesses", 2),
    strategy_row(
        "the-pawn-that-jails-a-rook", "King centralization", 0, origin="Larsen - Browne"
    ),
    strategy_row("walk-into-the-checks", "King centralization", 1, pro=True),
)


def strategy_listing(rows: tuple[dict[str, Any], ...] = STRATEGY_ROWS) -> str:
    return stream(page(component({"rows": list(rows)}), account=ACCOUNT))


#: Rook and pawns, Black's rook jailed on b8 by the pawn on b7.
JAILED_FEN = "1r6/1P3pp1/7p/4kP2/6K1/6P1/1R6/8 w - - 1 61"


def position_page(**overrides: Any) -> str:
    props = {
        "practiceSlug": "the-pawn-that-jails-a-rook",
        "titleAs": "h1",
        "title": "The pawn that jails a rook",
        "intro": "Your pawn chains his rook to b8.",
        "introHighlightSquares": ["b7", "b8"],
        "goal": "win",
        "initialFen": JAILED_FEN,
        "initialHeroColor": "w",
        "breadcrumb": {"label": "Strategy", "href": "/practice"},
    } | overrides
    return stream(page(component(props), account=ACCOUNT))


# Challenges.


def link(href: str, label: str) -> list:
    return component({"href": href, "children": [element("span", {"children": label})]})


def challenges_index() -> str:
    themes = [("checkmates", "Checkmates"), ("rook", "Rooks")]
    items = [
        element("li", {"children": link(f"/challenges/t/{t}", name)}, t)
        for t, name in themes
    ]
    return stream(
        page(link("/challenges", "Challenges"), element("ul", {"children": items}))
    )


def theme_page(categories: list[str]) -> str:
    items = [
        element("li", {"children": link(f"/challenges/{c}", c)}, c) for c in categories
    ]
    return stream(
        page(
            link("/challenges", "Endgame challenges"),
            element("ul", {"children": items}),
        )
    )


def crumbs(*labels: str) -> list:
    return element(
        "nav",
        {
            "children": [
                element("span", {"children": label}, f"{n}:{label}")
                for n, label in enumerate(labels)
            ]
        },
    )


def category_page(
    category: str, theme: str, title: str, captions: list[tuple[str, str]]
) -> str:
    """A category's Positions tab: the breadcrumb, a preview, then the list."""
    items = [
        element(
            "li",
            {"children": component({"fen": fen, "caption": caption, "children": []})},
            f"{category}-{n}-v2",
        )
        for n, (fen, caption) in enumerate(captions, start=1)
    ]
    preview = component({"fen": captions[0][0], "caption": captions[0][1]})
    return stream(
        page(
            crumbs("Endgame challenges", theme, title),
            preview,
            element("ol", {"children": items}),
        )
    )


ROOK_WIN = [
    ("8/2P5/4r3/k7/8/8/3K4/2R5 w - - 0 1", "Position 1 · Mate in 6"),
    ("8/K7/8/3k4/1P6/8/R6P/2r5 w - - 0 1", "Position 2 · Win"),
]
ROOK_DRAW = [("k7/8/8/1R6/5p1K/8/8/3r4 w - - 0 1", "Position 1 · Hold 8 moves")]
QUEEN_MATE = [("8/8/8/8/1Q6/8/k7/3K4 w - - 0 1", "Position 1 · Mate in 2")]


def challenge_pages() -> dict[str, str]:
    return {
        f"{ROOT}/challenges": challenges_index(),
        f"{ROOT}/challenges/t/checkmates": theme_page(["queen-mate"]),
        f"{ROOT}/challenges/t/rook": theme_page(["rook-win", "rook-draw"]),
        f"{ROOT}/challenges/queen-mate?tab=practice": category_page(
            "queen-mate", "Checkmates", "Queen mate", QUEEN_MATE
        ),
        f"{ROOT}/challenges/rook-win?tab=practice": category_page(
            "rook-win", "Rooks", "Winning rook endings", ROOK_WIN
        ),
        f"{ROOT}/challenges/rook-draw?tab=practice": category_page(
            "rook-draw", "Rooks", "Drawing rook endings", ROOK_DRAW
        ),
    }


class FakeFetcher:
    """Serves pages by URL, checking every request asks as the app does.

    Each request must be a flight request pinned to English; ``signed_in``
    says whether it must carry the session as well, or must not.
    """

    def __init__(self, pages: Mapping[str, str], *, signed_in: bool) -> None:
        self.pages = dict(pages)
        self.signed_in = signed_in
        self.gets: list[str] = []

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        self.gets.append(url)
        assert headers.get("RSC") == "1", "must ask for the flight stream"
        assert headers.get("Accept-Language") == "en", "must ask for English"
        cookies = headers.get("Cookie", "").split("; ")
        assert "chessendings-locale=en" in cookies, "must pin the English locale"
        assert (SESSION in cookies) is self.signed_in, "session sent wrongly"
        if url not in self.pages:
            raise FetchError(f"{url} failed: 404 (HTML page)", status=404)
        return self.pages[url]

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("chessendings pages are only read")


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read or refresh the session pgn-dl itself downloads with."""
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


@pytest.fixture
def guest(monkeypatch: pytest.MonkeyPatch) -> None:
    """No saved session, and no test may read a credential file."""
    monkeypatch.setattr(auth, "has_saved_session", lambda: False)

    async def no_cookie() -> str:
        raise AssertionError("a guest's run must not ask for the session")

    monkeypatch.setattr(auth, "session_cookie", no_cookie)


@pytest.fixture
def member(monkeypatch: pytest.MonkeyPatch) -> None:
    """A saved session, stubbed: no test may read the file or call Supabase."""
    monkeypatch.setattr(auth, "has_saved_session", lambda: True)

    async def stub_cookie() -> str:
        return SESSION

    monkeypatch.setattr(auth, "session_cookie", stub_cookie)


# Articles: the guides and each lesson's public page.


def paragraph(*parts: object, **props: Any) -> list:
    """A paragraph; parts may be text or inline elements, as the site writes."""
    return element(
        "p", {"children": list(parts) if len(parts) > 1 else parts[0], **props}
    )


def heading(text: str) -> list:
    return element(
        "div",
        {"className": "chapter-head", "children": element("h2", {"children": text})},
    )


def section(title: str, *children: object) -> list:
    return element("section", {"children": [heading(title), *children]}, title)


def replay(
    fen: str,
    moves: list[str],
    caption: str = "",
    *,
    pov: str = "w",
    annotations: list[dict[str, Any] | None] | None = None,
) -> list:
    """A diagram stepping through a line, in its figure."""
    props: dict[str, Any] = {"fen": fen, "pov": pov, "moves": moves, "locale": "en"}
    if caption:
        props |= {"caption": caption, "label": caption}
    props["annotations"] = annotations
    return element("figure", {"children": component(props)})


def static_figure(caption: str) -> list:
    """A diagram drawn on the server: a picture and its caption, no position."""
    board = element(
        "svg", {"children": [element("use", {"href": "#mbp-wk", "x": 0, "y": 0})]}
    )
    return element(
        "figure",
        {
            "children": [
                element("div", {"children": board}),
                element("figcaption", {"children": caption}),
            ]
        },
    )


def embedded_lesson(slug: str, fen: str, title: str = "A Lesson") -> list:
    return component(
        {
            "slug": slug,
            "title": title,
            "goal": "win",
            "initialFen": fen,
            "initialHeroColor": "w",
        }
    )


def article_page(title: str, lede: list[str], body: list, *, promo: tuple = ()) -> str:
    """An article page: the not-found page the layout carries, the hero, the body.

    ``promo`` sections follow the article outside it, as the site's own
    advertising for other pages does.
    """
    not_found = element(
        "main", {"children": [element("h1", {"children": "Stalemate."})]}
    )
    hero = element(
        "section",
        {
            "children": [
                element(
                    "div",
                    {
                        "children": [
                            element("h1", {"children": title}),
                            *(paragraph(text) for text in lede),
                            paragraph("Updated November 8, 2026"),
                        ]
                    },
                )
            ]
        },
    )
    crumbs = element("nav", {"children": [paragraph("Home › Guides")]})
    body_div = element(
        "div", {"children": [crumbs, element("div", {"children": body}), *promo]}
    )
    return stream(page(not_found, element("main", {"children": [hero, body_div]})))
