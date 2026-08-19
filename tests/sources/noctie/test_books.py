"""noctie opening books: URL parsing, the trie builder, and paged fetching.

Every read is served from the captured fixtures by a fake fetcher, and
``auth.id_token`` is replaced, so nothing here touches Google, noctie, or the
on-disk credential file.

``tests/fixtures/noctie/book_variations.json`` is a complete depth-<=12 subtree
of a real book: 132 documents, one of them the ``'>'`` root, no orphans, and 15
branch points. 89 carry a ``comment`` and 6 carry ``nags``. Those numbers are
asserted literally, since a builder that drops a branch or an annotation still
produces a plausible-looking file.

``book_doc.json`` carries the book's eight bookmarks, one per reply to 1.e4.
Each names a node of that same tree by its ``'>'``-prefixed SAN path, so the
expectations for a bookmark's size are counted off the variation fixture rather
than written down.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import GameItem, MoveNode
from pgn_dl.core.paths import COMBINED_NAME
from pgn_dl.core.pgn import game_to_pgn
from pgn_dl.core.registry import resolve_key, resolve_url
from pgn_dl.core.runner import Options, run
from pgn_dl.sources.noctie import auth
from pgn_dl.sources.noctie.books import (
    OpeningBooksExtractor,
    book_id_from,
    build_tree,
    slice_from,
)
from pgn_dl.sources.noctie.firestore import BASE, decode

from .conftest import FakeFetcher

pytestmark = pytest.mark.usefixtures("no_credentials")

FIX = Path(__file__).parents[2] / "fixtures" / "noctie"

BOOK_ID = "fbdadf0d-adac-4d9f-9557-83769b079737"
# noctie's own links are singular; the plural route exists too and gets pasted.
BOOK_URL = f"https://app.noctie.ai/opening-book/{BOOK_ID}"
BOOK_NAME = "Beginner's Guide to 1. e4"
VARIATIONS_PATH = f"openingBooks/{BOOK_ID}/openingBookVariations"

# The eight replies to 1.e4 the fixture covers, and the three-way branch deep in
# the Caro-Kann line. Both are read straight off the captured documents.
EIGHT_WAY = ["b6", "c5", "c6", "d5", "d6", "e5", "e6", "g6"]
CARO_LINE = "e4 c6 d4 d5 exd5 cxd5 Bd3 Nc6 c3 Nf6 h3"

# One node of the fixture whose document carries a comment, with the text
# itself. Chosen inside the 1...e5 bookmark's subtree so the same node proves
# the whole book and a bookmark slice both carry it.
COMMENTED_LINE = "e4 e5 Nf3 Nc6 Bc4"
COMMENTED_TEXT = "This leads to the starting position of the **Italian Game**."

# The one node of the fixture that carries a glyph inside that same subtree.
NAGGED_LINE = "e4 e5 Nf3 Nc6 Bc4 Nf6 d3 d5"
NAGGED_VALUE = 6

# The book document's own prose. The description is written with escapes
# because the source of this file is ASCII; the data is not, and that is the
# point of asserting on it.
BOOK_DESCRIPTION = "\u201cBest by test: 1.e4\u201d -Bobby Fischer"
BOOK_FULL_DESCRIPTION_OPENING = "Hi, I'm FM Hugo Wernberg from Sweden."
ROOT_COMMENT_OPENING = "Hey there, I'm **FM Hugo Wernberg** from Sweden."

# The book's bookmarks, in the order book_doc.json lists them: one per reply to
# 1.e4, each naming a node of the same tree.
BOOKMARK_PATHS = [
    ("bookmarks", "01-1-e5-king-s-pawn-game"),
    ("bookmarks", "02-1-c5-sicilian-defense"),
    ("bookmarks", "03-1-e6-french-defense"),
    ("bookmarks", "04-1-c6-caro-kann-defense"),
    ("bookmarks", "05-1-d5-scandinavian-defense"),
    ("bookmarks", "06-1-g6-modern-defense"),
    ("bookmarks", "07-1-d6-pirc-defense"),
    ("bookmarks", "08-1-b6-owen-defense"),
]


def _raw_variations() -> list[dict[str, Any]]:
    return json.loads((FIX / "book_variations.json").read_text(encoding="utf-8"))[
        "documents"
    ]


def _variations() -> list[dict[str, Any]]:
    """The fixture's documents as ``list_documents`` would hand them over."""
    return [decode(d["fields"]) for d in _raw_variations()]


def _node_count(node: MoveNode) -> int:
    """Nodes below the root, i.e. one per non-root variation document."""
    return sum(1 + _node_count(c) for c in node.children)


def _pgn_node_count(node: chess.pgn.GameNode) -> int:
    return sum(1 + _pgn_node_count(v) for v in node.variations)


def _walk(root: MoveNode, line: str) -> MoveNode:
    """The node reached by following a space-separated SAN line from the root."""
    node = root
    for san in line.split():
        node = next(c for c in node.children if c.san == san)
    return node


def _subtree_size(variation: str) -> int:
    """Documents at ``variation`` or below it, counted off the fixture."""
    paths = [v["variation"] for v in _variations()]
    return sum(1 for p in paths if p == variation or p.startswith(f"{variation} "))


def _bookmark_value(bookmark: dict[str, str] | str) -> dict[str, Any]:
    """One bookmark in Firestore's encoding. A plain string is a malformed one."""
    if isinstance(bookmark, str):
        return {"stringValue": bookmark}
    fields = {k: {"stringValue": v} for k, v in bookmark.items()}
    return {"mapValue": {"fields": fields}}


def _book_doc(
    bookmarks: list[dict[str, str] | str],
    name: str | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    """The book document, with its bookmarks and optionally its name replaced.

    ``drop`` removes fields outright, which is how a book that carries no prose
    of its own is written: upstream omits the key rather than emptying it.
    """
    doc = json.loads((FIX / "book_doc.json").read_text(encoding="utf-8"))
    if name is not None:
        doc["fields"]["name"] = {"stringValue": name}
    doc["fields"]["bookmarks"] = {
        "arrayValue": {"values": [_bookmark_value(b) for b in bookmarks]}
    }
    for field in drop:
        doc["fields"].pop(field, None)
    return json.dumps(doc)


def _var(
    variation: str,
    order: int = 0,
    comment: str | None = None,
    nags: list[Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "variation": variation,
        "order": order,
        "halfMoves": len(variation.split()) - 1,
    }
    # Absent rather than empty: most documents carry neither key at all.
    if comment is not None:
        doc["comment"] = comment
    if nags is not None:
        doc["nags"] = nags
    return doc


def _variations_page(docs: list[dict[str, Any]]) -> str:
    """Plain variation documents in Firestore's encoding, as one listing page."""

    def value(v: Any) -> dict[str, Any]:
        if isinstance(v, int):
            return {"integerValue": str(v)}
        if isinstance(v, list):
            return {"arrayValue": {"values": [value(x) for x in v]}}
        return {"stringValue": v}

    return json.dumps(
        {
            "documents": [
                {
                    "name": f"{VARIATIONS_PATH}/{i}",
                    "fields": {k: value(v) for k, v in d.items()},
                }
                for i, d in enumerate(docs)
            ]
        }
    )


def _commented_nodes(node: MoveNode) -> int:
    """Nodes carrying prose, the root included; it holds the book's preface."""
    return bool(node.post_comment) + sum(_commented_nodes(c) for c in node.children)


def _documents_with_comments() -> int:
    """Fixture documents carrying a non-empty ``comment``."""
    return sum(1 for v in _variations() if v.get("comment"))


def _pgn_walk(game: chess.pgn.GameNode, line: str) -> chess.pgn.GameNode:
    """The parsed-PGN node reached by following a space-separated SAN line."""
    node = game
    for san in line.split():
        node = next(v for v in node.variations if v.san() == san)
    return node


def _fetcher() -> FakeFetcher:
    return FakeFetcher(
        {
            f"{BASE}/openingBooks/{BOOK_ID}": (FIX / "book_doc.json").read_text(
                encoding="utf-8"
            ),
            f"{BASE}/{VARIATIONS_PATH}": (FIX / "book_variations.json").read_text(
                encoding="utf-8"
            ),
        }
    )


@pytest.mark.parametrize(
    "target",
    [
        BOOK_URL,
        f"https://app.noctie.ai/opening-books/{BOOK_ID}",
        f"https://noctie.ai/opening-book/{BOOK_ID}",
        f"{BOOK_URL}/",
        f"{BOOK_URL}?ref=share",
        f"{BOOK_URL}#tree",
        BOOK_ID,
    ],
)
def test_book_id_from_accepts_every_shape_of_target(target: str) -> None:
    assert book_id_from(target) == BOOK_ID


@pytest.mark.parametrize(
    "target",
    [
        f"https://example.com/opening-book/{BOOK_ID}",
        "https://app.noctie.ai/opening-books",
        "https://app.noctie.ai/opening-book/not-a-uuid",
        f"https://app.noctie.ai/themes/{BOOK_ID}",
        "",
    ],
)
def test_book_id_from_rejects_anything_else(target: str) -> None:
    # noctie's SPA answers 200 for every path, so a wrong target cannot be
    # caught by the response: the pattern is the only guard.
    with pytest.raises(ValueError):
        book_id_from(target)


def test_from_url_declines_a_foreign_url_without_raising() -> None:
    assert OpeningBooksExtractor.from_url("https://www.chess.com/lessons/x") is None
    assert isinstance(OpeningBooksExtractor.from_url(BOOK_URL), OpeningBooksExtractor)


def test_build_tree_links_each_node_to_its_san_prefix() -> None:
    root = build_tree(
        [
            _var(">"),
            _var("> e4"),
            _var("> e4 e5", order=0),
            _var("> e4 c5", order=1),
            _var("> e4 e5 Nf3"),
        ]
    )
    assert root.san is None
    # children[0] is the mainline; game_to_pgn writes the rest as variations.
    assert [c.san for c in root.children] == ["e4"]
    e4 = root.children[0]
    assert [c.san for c in e4.children] == ["e5", "c5"]
    assert [c.san for c in e4.children[0].children] == ["Nf3"]
    assert e4.children[1].children == []


def test_build_tree_orders_siblings_by_order_then_san() -> None:
    # `order` has gaps and repeats, so it is not a per-parent index and cannot
    # be the whole sort key without leaving the output unstable.
    root = build_tree(
        [
            _var(">"),
            _var("> e4"),
            _var("> e4 e5", order=5),
            _var("> e4 e6", order=0),
            _var("> e4 c5", order=0),
        ]
    )
    assert [c.san for c in root.children[0].children] == ["c5", "e6", "e5"]


def test_build_tree_is_insensitive_to_document_arrival_order() -> None:
    docs = [_var(">"), _var("> e4"), _var("> e4 e5"), _var("> e4 e5 Nf3")]
    forward = build_tree(docs)
    backward = build_tree(list(reversed(docs)))
    assert _node_count(forward) == _node_count(backward) == 3
    assert [c.san for c in backward.children[0].children[0].children] == ["Nf3"]


def test_a_slices_replayed_prefix_shares_no_list_with_the_book() -> None:
    # `replace` copies field references, so a prefix node built from one would
    # hold the book's own `nags`/`arrows`/`highlights` objects. Nothing mutates
    # a built tree today, so this is a footgun rather than a live bug: the first
    # code to edit a bookmark's annotations in place would silently edit the
    # whole book's, and every other bookmark cut through the same move.
    root = build_tree(
        [
            _var(">"),
            _var("> e4", nags=["$1"]),
            _var("> e4 e5"),
        ]
    )
    sliced = slice_from(root, ["e4", "e5"])
    assert sliced is not None

    prefix = sliced.children[0]
    original = root.children[0]
    assert prefix.nags == original.nags == [1]
    assert prefix.nags is not original.nags
    assert prefix.arrows is not original.arrows
    assert prefix.highlights is not original.highlights


def test_a_variation_whose_parent_is_missing_fails_the_item() -> None:
    # Dropping it would emit a truncated tree that looks like a clean download.
    # FetchError has the runner tally the item as failed instead.
    with pytest.raises(FetchError):
        build_tree([_var(">"), _var("> e4"), _var("> d4 d5 c4")])


def test_a_variation_without_the_root_marker_fails_the_item() -> None:
    with pytest.raises(FetchError):
        build_tree([_var(">"), {"variation": "e4 e5", "order": 0}])


def test_a_duplicated_variation_fails_the_item() -> None:
    # Two documents for one path would otherwise attach two same-SAN siblings
    # to the parent, with everything deeper hanging off the second: a rendered
    # PGN with an empty twin branch that reads as a real alternative.
    with pytest.raises(FetchError):
        build_tree(
            [
                _var(">"),
                _var("> e4"),
                _var("> e4 e5", order=0),
                _var("> e4 e5", order=1),
            ]
        )


def test_half_moves_is_ignored_in_favour_of_the_token_count() -> None:
    # Depth comes from the SAN path alone. halfMoves is upstream's own ply
    # count and nothing here reads it, so a wrong one must not move a node.
    docs = [_var(">"), _var("> e4"), _var("> e4 e5"), _var("> e4 e5 Nf3")]
    docs[-1]["halfMoves"] = 99
    root = build_tree(docs)
    assert _walk(root, "e4 e5 Nf3").san == "Nf3"
    assert _node_count(root) == 3


def test_build_tree_keeps_every_node_of_the_real_book() -> None:
    variations = _variations()
    assert len(variations) == 132  # 131 moves plus the '>' root document
    root = build_tree(variations)
    assert _node_count(root) == 131


def test_build_tree_rebuilds_the_real_books_branches() -> None:
    root = build_tree(_variations())
    assert [c.san for c in root.children] == ["e4"]

    e4 = root.children[0]
    assert sorted(c.san or "" for c in e4.children) == EIGHT_WAY
    # Sibling order comes from `order`: 1...e5 is the mainline reply.
    assert [c.san for c in e4.children] == [
        "e5",
        "c5",
        "e6",
        "c6",
        "d5",
        "g6",
        "d6",
        "b6",
    ]

    caro = _walk(root, CARO_LINE)
    assert sorted(c.san or "" for c in caro.children) == ["e5", "e6", "g6"]
    assert [c.san for c in caro.children] == ["g6", "e5", "e6"]

    scandi = _walk(root, "e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6 Nf3 Bg4 h3")
    assert sorted(c.san or "" for c in scandi.children) == ["Bh5", "Bxf3", "Qh5"]

    pirc = _walk(root, "e4 g6 d4 Bg7 Nf3 d6 Be3 Nf6 Nc3 O-O h3")
    assert sorted(c.san or "" for c in pirc.children) == ["Nbd7", "Nc6", "c6"]


def test_the_tree_survives_a_round_trip_through_pgn() -> None:
    # Every SAN has to be legal in its own position, or python-chess drops the
    # move and everything under it without saying so.
    collection = OpeningBooksExtractor.collection(slug="s", name=BOOK_NAME)
    item = GameItem(root=build_tree(_variations()))
    text = game_to_pgn(collection, item)

    game = chess.pgn.read_game(io.StringIO(text))
    assert game is not None
    assert len(game.variations) == 1
    e4 = game.variations[0]
    assert e4.san() == "e4"
    # The eight replies must come back as siblings, not as a single line.
    assert sorted(v.san() for v in e4.variations) == EIGHT_WAY

    node = e4
    for san in CARO_LINE.split()[1:]:
        node = next(v for v in node.variations if v.san() == san)
    assert sorted(v.san() for v in node.variations) == ["e5", "e6", "g6"]

    # An illegal SAN would have python-chess drop that move and everything
    # under it; the count proves the whole tree made the round trip.
    assert game.errors == []
    assert _pgn_node_count(game) == 131


def test_a_documents_comment_becomes_the_move_it_names() -> None:
    root = build_tree(_variations())
    node = _walk(root, COMMENTED_LINE)
    # The prose describes the position the move reaches, so it is the comment
    # that follows the move rather than the one that introduces it.
    assert COMMENTED_TEXT in node.post_comment
    assert node.pre_comment == ""


def test_every_commented_document_becomes_a_commented_node() -> None:
    # A builder that read `comment` on only some nodes would still produce a
    # plausible file, just a quieter book.
    root = build_tree(_variations())
    # The literal is there so a regenerated fixture fails loudly rather than
    # letting both sides drift to zero together.
    assert _commented_nodes(root) == _documents_with_comments()
    assert _documents_with_comments() == 89


def test_the_root_documents_comment_lands_on_the_root_node() -> None:
    # The root is not a move, so its prose is the book's preface rather than an
    # annotation. It rides on the root node, which core.pgn never renders, and
    # fetch() lifts it into the game comment.
    root = build_tree(_variations())
    assert ROOT_COMMENT_OPENING in root.post_comment
    assert root.san is None


def test_comments_and_nags_do_not_perturb_the_shape_of_the_tree() -> None:
    # Parentage is the SAN path and sibling order is `order`; neither reads
    # `comment` or `nags`.
    root = build_tree(_variations())
    assert _node_count(root) == 131
    assert [c.san for c in root.children] == ["e4"]
    assert sorted(c.san or "" for c in root.children[0].children) == EIGHT_WAY
    assert [c.san for c in root.children[0].children] == [
        "e5",
        "c5",
        "e6",
        "c6",
        "d5",
        "g6",
        "d6",
        "b6",
    ]


def test_glyphs_are_parsed_into_the_integers_pgn_writes_back() -> None:
    root = build_tree(_variations())
    assert _walk(root, NAGGED_LINE).nags == [NAGGED_VALUE]
    # Only six documents in the fixture carry one, so an over-eager parser that
    # invented a glyph per move would show up here.
    nagged = sum(1 for v in _variations() if v.get("nags"))
    assert nagged == 6


def test_a_comment_survives_the_round_trip_through_pgn() -> None:
    collection = OpeningBooksExtractor.collection(slug="s", name=BOOK_NAME)
    item = GameItem(root=build_tree(_variations()))
    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))

    assert game is not None
    assert game.errors == []
    node = _pgn_walk(game, COMMENTED_LINE)
    # python-chess trims the braces' padding on the way back in; the text
    # between them is the book's own, markdown included.
    assert node.comment == COMMENTED_TEXT
    assert _pgn_walk(game, NAGGED_LINE).nags == {NAGGED_VALUE}
    # A glyph is written as $N, which is what a reader outside python-chess sees.
    assert f"${NAGGED_VALUE}" in game_to_pgn(collection, item)


@pytest.mark.parametrize("nags", [["2"], ["junk"], ["$"], ["$2x"], [""], "$2", 2, None])
def test_a_glyph_that_cannot_be_parsed_is_dropped_not_raised(nags: Any) -> None:
    # A repertoire is worth more than a glyph. Anything not in `$N` form is left
    # off the node, and the move, with every move under it, is still written.
    root = build_tree(
        [_var(">"), _var("> e4", nags=nags), _var("> e4 e5"), _var("> e4 e5 Nf3")]
    )
    assert _walk(root, "e4").nags == []
    assert _node_count(root) == 3


def test_the_parsable_glyphs_of_a_mixed_list_are_kept() -> None:
    root = build_tree([_var(">"), _var("> e4", nags=["$1", "junk", "$14"])])
    assert _walk(root, "e4").nags == [1, 14]


def test_markdown_and_non_ascii_prose_pass_through_unaltered() -> None:
    # Book prose is not ASCII and is not plain text: curly quotes, em dashes and
    # markdown emphasis are the author's, and nothing here may tidy them.
    text = "\u201cBest by test\u201d \u2014 **1.e4**, *always*."
    root = build_tree([_var(">"), _var("> e4", comment=text)])
    assert _walk(root, "e4").post_comment == text

    collection = OpeningBooksExtractor.collection(slug="s", name=BOOK_NAME)
    item = GameItem(root=root)
    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    assert game.variations[0].comment == text


async def test_discover_returns_the_whole_book_and_one_item_per_bookmark() -> None:
    collection, refs = await OpeningBooksExtractor(BOOK_URL).discover(_fetcher())
    # The whole book is one game, not one file per variation; the bookmarks are
    # cuts of that same game, and cost no extra request to enumerate because
    # they ride along on the book document discover() already reads.
    assert len(refs) == 9
    assert refs[0].id == BOOK_ID
    assert refs[0].path == ("beginner-s-guide-to-1-e4",)
    assert [r.path for r in refs[1:]] == BOOKMARK_PATHS
    # Numbering keeps the bookmarks in the book's own order once written out.
    assert len({r.id for r in refs}) == 9
    assert collection.name == BOOK_NAME
    assert collection.source == "noctie"
    assert collection.kind == "opening-books"
    # Book names are not unique upstream, so the slug carries the id.
    assert collection.slug == f"beginner-s-guide-to-1-e4-{BOOK_ID[:8]}"
    # Unlike themes, this source emits a move tree, so collection headers do
    # reach the written file.
    assert collection.headers["Event"] == BOOK_NAME
    assert collection.headers["Site"] == "noctie.ai"


async def test_fetch_returns_a_move_tree_and_identifies_the_book() -> None:
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])

    # The move-tree seam, not the pre-rendered-PGN one.
    assert item.pgn is None
    assert _node_count(item.root) == 131
    assert item.headers["NoctieBookId"] == BOOK_ID
    # The book is a White repertoire; that is worth carrying into the file.
    assert item.headers["White"] == BOOK_NAME
    assert item.headers["Black"] == "?"


async def test_the_rendered_file_carries_the_books_identity() -> None:
    # Unlike themes, this source hands over a move tree, so the runner renders
    # the PGN and Collection.headers really are applied.
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])

    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    assert game.headers["Event"] == BOOK_NAME  # from the collection
    assert game.headers["Site"] == "noctie.ai"
    assert game.headers["NoctieBookId"] == BOOK_ID  # from the item
    assert game.headers["White"] == BOOK_NAME
    assert game.headers["Black"] == "?"


async def test_a_bookmark_replays_its_path_before_its_own_lines() -> None:
    # "Everything from 1...e5" has to open 1. e4 e5 and carry on. Anchoring it
    # to a FEN instead would produce a file that no longer says how the
    # position arose, and that the book's other files could not be read beside.
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])

    assert item.pgn is None
    assert item.starting_fen == chess.STARTING_FEN
    mainline = item.root
    opening = []
    for _ in range(2):
        mainline = mainline.children[0]
        opening.append(mainline.san)
    assert opening == ["e4", "e5"]
    # The bookmark node keeps its whole subtree; the only nodes added are the
    # moves leading to it, here just 1. e4.
    assert _node_count(item.root) == _subtree_size("> e4 e5") + 1

    assert item.headers["Event"] == "1...e5 - King's Pawn Game"
    assert item.headers["NoctieBookId"] == BOOK_ID
    assert item.headers["NoctieBook"] == BOOK_NAME


async def test_a_bookmark_renders_as_a_readable_game() -> None:
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])

    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    # An illegal SAN would have python-chess drop that move and everything
    # under it, so a clean re-parse is what proves the replayed prefix joins up
    # with the subtree instead of merely looking plausible.
    assert game.errors == []
    assert "FEN" not in game.headers
    assert [n.san() for n in game.mainline()][:2] == ["e4", "e5"]
    assert _pgn_node_count(game) == _subtree_size("> e4 e5") + 1
    # The file names the bookmark, and still says which book it was cut from.
    assert game.headers["Event"] == "1...e5 - King's Pawn Game"
    assert game.headers["NoctieBook"] == BOOK_NAME
    assert game.headers["NoctieBookId"] == BOOK_ID


async def test_a_bookmarks_description_becomes_the_game_comment() -> None:
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [{"name": "Italian", "description": "Slow but sound.", "variation": "> e4 e5"}]
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])
    assert item.comment == "Slow but sound."


async def test_the_books_own_prose_becomes_the_game_comment() -> None:
    # Three pieces of prose, all of them game-level: the blurb, the long
    # description, and the author's preface on the root document. None belongs
    # in a header: fullDescription is multi-line markdown, and a PGN header is a
    # single line.
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])

    assert BOOK_DESCRIPTION in item.comment
    assert BOOK_FULL_DESCRIPTION_OPENING in item.comment
    assert ROOT_COMMENT_OPENING in item.comment
    # A stable order, so re-running a download does not rewrite the file.
    assert item.comment.index(BOOK_DESCRIPTION) < item.comment.index(
        BOOK_FULL_DESCRIPTION_OPENING
    )
    assert item.comment.index(BOOK_FULL_DESCRIPTION_OPENING) < item.comment.index(
        ROOT_COMMENT_OPENING
    )
    assert "\n\n" in item.comment  # blank-line separated, not run together
    # And none of it reaches a header, not even the one-line blurb: a PGN header
    # value is a single line, and a reader shows it as the game's identity.
    values = " ".join([*item.headers.values(), *collection.headers.values()])
    assert BOOK_DESCRIPTION not in values
    assert BOOK_FULL_DESCRIPTION_OPENING not in values
    assert ROOT_COMMENT_OPENING not in values


async def test_the_books_prose_reaches_the_rendered_file() -> None:
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])

    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    assert game.errors == []
    assert BOOK_DESCRIPTION in game.comment
    assert ROOT_COMMENT_OPENING in game.comment
    assert _pgn_walk(game, COMMENTED_LINE).comment == COMMENTED_TEXT


async def test_a_missing_piece_of_prose_is_skipped_rather_than_left_blank() -> None:
    # Most books carry only some of the three. Absent pieces must not leave
    # leading, trailing or doubled blank lines in the comment.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [], drop=("description", "fullDescription")
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])

    assert item.comment.startswith(ROOT_COMMENT_OPENING)
    assert item.comment == item.comment.strip()
    assert BOOK_FULL_DESCRIPTION_OPENING not in item.comment


async def test_a_book_with_no_prose_at_all_has_no_game_comment() -> None:
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [], drop=("description", "fullDescription")
    )
    fetcher.pages[f"{BASE}/{VARIATIONS_PATH}"] = json.dumps(
        {
            "documents": [
                d
                for d in _raw_variations()
                if d["fields"]["variation"]["stringValue"] != ">"
            ]
        }
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])
    assert item.comment == ""


async def test_a_bookmark_carries_the_commentary_on_its_own_nodes() -> None:
    # A slice re-uses the book's MoveNode objects rather than copying them, so
    # the annotations come along with them.
    fetcher = _fetcher()
    extractor = OpeningBooksExtractor(BOOK_URL)
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])  # 1...e5

    assert _walk(item.root, COMMENTED_LINE).post_comment.strip() == COMMENTED_TEXT
    assert _walk(item.root, NAGGED_LINE).nags == [NAGGED_VALUE]

    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    assert game.errors == []
    assert _pgn_walk(game, COMMENTED_LINE).comment == COMMENTED_TEXT
    assert _pgn_walk(game, NAGGED_LINE).nags == {NAGGED_VALUE}


async def test_a_bookmarks_replayed_prefix_keeps_its_own_commentary() -> None:
    # The moves leading into a bookmark are rebuilt as fresh nodes, so that the
    # slice does not hand out the book's own nodes with extra children. Rebuilt
    # bare they would arrive stripped of everything the author wrote about them,
    # and in a real repertoire the paragraph introducing the whole book ("Who is
    # the Caro-Kann for? ...") sits on the `> e4` document, one ply above every
    # bookmark in it. The fixture's `> e4` carries no comment, so only a
    # synthetic book shows this.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [{"name": "Caro-Kann", "description": "", "variation": "> e4 c6"}]
    )
    fetcher.pages[f"{BASE}/{VARIATIONS_PATH}"] = _variations_page(
        [
            _var(">"),
            _var("> e4", comment="Who is the Caro-Kann for?", nags=["$1"]),
            _var("> e4 c6", comment="The Caro-Kann."),
            _var("> e4 c6 d4", comment="We take the centre."),
        ]
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    collection, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])

    prefix = item.root.children[0]
    assert prefix.san == "e4"
    assert prefix.post_comment == "Who is the Caro-Kann for?"
    assert prefix.nags == [1]
    # A copy, not the book's own node: the whole-book file must not grow this
    # bookmark's children.
    whole = await extractor.fetch(fetcher, refs[0])
    assert prefix is not _walk(whole.root, "e4")
    assert _node_count(whole.root) == 3

    game = chess.pgn.read_game(io.StringIO(game_to_pgn(collection, item)))
    assert game is not None
    assert game.errors == []
    assert game.variations[0].comment == "Who is the Caro-Kann for?"
    assert game.variations[0].nags == {1}
    assert _pgn_walk(game, "e4 c6").comment == "The Caro-Kann."
    assert _pgn_walk(game, "e4 c6 d4").comment == "We take the centre."


async def test_a_bookmark_does_not_inherit_the_whole_books_preface() -> None:
    # Eight bookmark files each repeating the book's introduction would be eight
    # copies of the same page. The bookmark's own description is its comment.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [{"name": "Italian", "description": "Slow but sound.", "variation": "> e4 e5"}]
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])

    assert item.comment == "Slow but sound."
    assert ROOT_COMMENT_OPENING not in item.comment
    assert BOOK_DESCRIPTION not in item.comment


async def test_a_bookmark_outside_the_tree_fails_only_that_item() -> None:
    # Same rule the trie builder applies to an orphan: emitting the two moves
    # of the prefix and nothing else would be a file the runner tallies as a
    # clean download and every later run skips.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [
            {"name": "Alekhine", "description": "", "variation": "> e4 Nf6"},
            {"name": "King's Pawn Game", "description": "", "variation": "> e4 e5"},
        ]
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)

    with pytest.raises(FetchError) as exc:
        await extractor.fetch(fetcher, refs[1])
    assert "Alekhine" in str(exc.value)
    assert "e4 Nf6" in str(exc.value)

    # The neighbouring items are untouched.
    assert _node_count((await extractor.fetch(fetcher, refs[0])).root) == 131
    assert _node_count((await extractor.fetch(fetcher, refs[2])).root) == (
        _subtree_size("> e4 e5") + 1
    )


async def test_a_bookmark_that_is_not_a_map_fails_only_its_own_item() -> None:
    # An unguarded b.get() would raise AttributeError out of discover(), which
    # runner.run does not catch, so one malformed entry would cost the
    # whole-book item too.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [
            "> e4 e5",
            {"name": "King's Pawn Game", "description": "", "variation": "> e4 e5"},
        ]
    )
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)

    # Still an item, so the run's tally says a bookmark was not written.
    assert [r.path for r in refs[1:]] == [
        ("bookmarks", "01-untitled"),
        ("bookmarks", "02-king-s-pawn-game"),
    ]
    with pytest.raises(FetchError) as exc:
        await extractor.fetch(fetcher, refs[1])
    assert BOOK_ID in str(exc.value)

    # The whole book, and the bookmark beside it, are untouched.
    assert _node_count((await extractor.fetch(fetcher, refs[0])).root) == 131
    assert _node_count((await extractor.fetch(fetcher, refs[2])).root) == (
        _subtree_size("> e4 e5") + 1
    )


async def test_a_book_named_all_does_not_overwrite_its_own_file(tmp_path: Path) -> None:
    # combine_collection_dir builds all.pgn out of every other PGN beside it and
    # then writes it, so an item written to that same name is destroyed by the
    # combine: the run reports both files written, all.pgn holds only the
    # bookmark, and every later run skips both. Silent, and permanent.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc(
        [{"name": "King's Pawn Game", "description": "", "variation": "> e4 e5"}],
        name="All",
    )
    report = await run(
        OpeningBooksExtractor(BOOK_URL),
        fetcher,
        Options(out=tmp_path, concurrency=2, delay=0.0),
        echo=lambda _: None,
    )

    assert report.written == 2
    root = tmp_path / "noctie" / "opening-books" / f"all-{BOOK_ID[:8]}"
    book = next(p for p in root.glob("*.pgn") if p.name != COMBINED_NAME)
    game = chess.pgn.read_game(io.StringIO(book.read_text(encoding="utf-8")))
    assert game is not None
    assert _pgn_node_count(game) == 131  # the whole book, not the bookmark


async def test_a_book_with_no_bookmarks_is_still_one_item() -> None:
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/openingBooks/{BOOK_ID}"] = _book_doc([])
    _, refs = await OpeningBooksExtractor(BOOK_URL).discover(fetcher)
    assert len(refs) == 1
    # No empty bookmarks/ directory: the path has one segment, so the file
    # lands beside the collection's own.
    assert refs[0].path == ("beginner-s-guide-to-1-e4",)


async def test_a_book_with_no_variations_fails_the_item() -> None:
    # An empty tree would have the runner write a moveless file and then skip
    # it on every later run, so a re-run could never repair it.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/{VARIATIONS_PATH}"] = json.dumps({})
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    with pytest.raises(FetchError) as exc:
        await extractor.fetch(fetcher, refs[0])
    assert BOOK_ID in str(exc.value)
    assert "variations" in str(exc.value)


async def test_fetch_follows_pagination_to_the_end() -> None:
    # A capped request returns exactly pageSize documents and hides the rest,
    # which for a move tree means silently dropping whole branches.
    raw = _raw_variations()
    half = len(raw) // 2
    pages = [
        json.dumps({"documents": raw[:half], "nextPageToken": "page+two"}),
        json.dumps({"documents": raw[half:]}),
    ]

    class Paging(FakeFetcher):
        async def get_text(self, url, *, headers):
            self.gets.append(url)
            if url.split("?", 1)[0] != f"{BASE}/{VARIATIONS_PATH}":
                return await super().get_text(url, headers=headers)
            return pages[1] if "pageToken=" in url else pages[0]

    fetcher = Paging(_fetcher().pages)
    extractor = OpeningBooksExtractor(BOOK_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])

    listings = [u for u in fetcher.gets if VARIATIONS_PATH in u]
    assert len(listings) == 2, "the second page must be requested"
    assert "pageToken=page%2Btwo" in listings[1]
    # Nothing lost across the page boundary.
    assert _node_count(item.root) == 131


async def test_an_expired_token_is_re_minted_for_this_source_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The retry lives on the shared base, so books get it as themes do: a book
    # can take longer than the hour its ID token lasts.
    minted: list[str] = []

    async def fake_id_token() -> str:
        minted.append(f"tok{len(minted) + 1}")
        return minted[-1]

    monkeypatch.setattr(auth, "id_token", fake_id_token)

    class Expiring(FakeFetcher):
        def __init__(self, pages: dict[str, str]) -> None:
            super().__init__(pages)
            self.expired = True

        async def get_text(self, url, *, headers):
            self.gets.append(url)
            if self.expired:
                self.expired = False
                raise FetchError(f"{url} failed: 401 expired", status=401)
            return self.pages[url.split("?", 1)[0]]

    fetcher = Expiring(_fetcher().pages)
    _, refs = await OpeningBooksExtractor(BOOK_URL).discover(fetcher)

    assert len(refs) == 9
    assert minted == ["tok1", "tok2"]
    book_url = f"{BASE}/openingBooks/{BOOK_ID}"
    assert fetcher.gets == [book_url, book_url]


def test_the_extractor_is_registered_by_key_and_by_url() -> None:
    # The discovery-time access guards (paywall 403, missing login, and their
    # non-403 counterparts) live in test_access.py, shared with themes.
    assert resolve_key("noctie:opening-books") is OpeningBooksExtractor
    assert isinstance(resolve_url(BOOK_URL), OpeningBooksExtractor)
