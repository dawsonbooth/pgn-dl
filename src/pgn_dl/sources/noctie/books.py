"""noctie opening books.

A book is one move tree, stored one document per node. Each document carries the
node's whole path from the start position as a ``>`` root marker followed by
space-separated SAN::

    {"variation": "> e4 e5 Nf3", "halfMoves": 3, "order": 0}

so rebuilding the book is building a trie of those paths. That fills
``GameItem.root`` rather than ``GameItem.pgn``, the opposite seam from themes,
which arrive pre-rendered.

Those same documents carry the book's teaching: a ``comment`` on the move (two
thirds of them on the books sampled, up to a couple of thousand characters of
markdown) and sometimes ``nags``, PGN evaluation glyphs stored as ``"$2"``. A
book without them is a bare list of moves, so they ride onto the nodes. The
document for the ``>`` root is not a move: its comment is the author's preface,
and joins the book's ``description`` and ``fullDescription`` as the game comment.

A book also carries bookmarks: named cuts of that one tree, addressed by the
same ``>``-prefixed SAN path, which on the sample book partition it one per
reply to 1.e4. Each becomes a file of its own alongside the whole book, opening
with the moves that lead to the bookmarked node so it reads as a game rather
than a fragment. They ride along on the book document, so enumerating them costs
no request, and every item is a view of the same listing, hence the cache.

Parentage comes from the SAN path, never from ``order``. A sweep of 40 books
found ``order`` values running 0..7 with gaps, so it is not a per-parent sibling
index; it serves only to order siblings, with the SAN as a tiebreak so two runs
over a book with repeated ``order`` values write the same file. Paging is
mandatory: 17 of those 40 books filled a 300-document page, and a truncated
listing does not look truncated, only like a shorter book with whole branches
missing. Book names are not unique, so the output slug carries a slice of the id.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef, MoveNode
from ...core.paths import COMBINED_NAME, slugify
from .base import SITE, NoctieExtractor, id_from, target_pattern
from .firestore import get_document, list_documents

#: The token that stands for the position before any move.
ROOT_MARKER = ">"

#: Prefix of a bookmark item's ``ItemRef.id``, followed by the bookmark's
#: position in the book's ``bookmarks`` array. The whole-book item carries the
#: book id itself, a UUID, so ``fetch`` can tell the two kinds apart by the
#: prefix alone. The position, rather than the bookmark's ``variation``, because
#: nothing upstream stops two bookmarks naming the same node.
BOOKMARK_ID = "bookmark:"

# Which PGN header names the side the book is a repertoire for.
_REPERTOIRE_SIDE = {"white": "White", "black": "Black"}

_TARGET_RE = target_pattern("opening-books?")

#: A ``nags`` entry, which upstream stores in PGN's own glyph form (``"$2"``).
#: ``MoveNode.nags`` holds the integers, and ``core.pgn`` writes them back out
#: as ``$N``.
_NAG_RE = re.compile(r"\$(\d+)")

# What this source's material is called, in a rejected target and in the
# explanation of a denied read.
_NOUN = "opening book"


def book_id_from(target: str) -> str:
    """The book id in a noctie opening-book URL, or a bare id."""
    return id_from(target, _TARGET_RE, _NOUN)


def _bookmark_name(bookmark: object) -> str:
    """A bookmark's display name, or "" when the entry is not a map at all."""
    return str(bookmark.get("name") or "") if isinstance(bookmark, dict) else ""


def _prose(*parts: str) -> str:
    """Several pieces of game-level prose as one comment, blank-line separated.

    Whichever pieces a book does not carry are skipped, so a book with only a
    preface does not open with blank lines. Only the joins are tidied; what is
    inside a piece (markdown, curly quotes, the author's own line breaks) is the
    author's and passes through as written.
    """
    return "\n\n".join(stripped for p in parts if (stripped := p.strip()))


def _book_file_slug(name: str) -> str:
    """The whole book's own filename, kept clear of the combined file.

    ``combine_collection_dir`` concatenates every other PGN under the collection
    into ``all.pgn`` and then writes it, so a book named "All" would otherwise
    write its tree to that same name and have it overwritten. Only that one name
    is steered around; every other book keeps the filename it already had.
    """
    slug = slugify(name)
    return f"{slug}-book" if f"{slug}.pgn" == COMBINED_NAME else slug


def _san_path(variation: object) -> tuple[str, ...]:
    """The SAN tokens of one ``variation`` string, root marker removed."""
    tokens = str(variation or "").split()
    if not tokens or tokens[0] != ROOT_MARKER:
        # Every node's path is absolute, so a missing marker means the encoding
        # is not the one this builder knows, and guessing at it would produce a
        # tree that is wrong rather than absent.
        raise FetchError(f"noctie variation {variation!r} has no {ROOT_MARKER!r} root")
    return tuple(tokens[1:])


def _comment(document: dict[str, Any]) -> str:
    """One document's prose, as the author wrote it.

    Absent, null or non-string reads as none. Nothing here tidies the text:
    markdown, curly quotes and the author's own line breaks are the book's. The
    format changes it twice further downstream, though. ``core.pgn`` substitutes
    ``(`` and ``)`` for braces, which PGN cannot carry inside a comment, and
    python-chess strips the whitespace around a comment when reading one back.
    """
    comment = document.get("comment")
    return comment if isinstance(comment, str) else ""


def _nags(document: dict[str, Any]) -> list[int]:
    """One document's evaluation glyphs as the integers ``MoveNode`` holds.

    An entry that is not in ``$N`` form is dropped rather than raised on: the
    glyph vocabulary is open-ended and this is upstream's free-text field, so
    the move it sits on is still worth writing without it.
    """
    values = document.get("nags")
    if not isinstance(values, list):
        return []
    parsed = [_NAG_RE.fullmatch(v) for v in values if isinstance(v, str)]
    return [int(m.group(1)) for m in parsed if m is not None]


def build_tree(variations: Sequence[dict[str, Any]]) -> MoveNode:
    """Rebuild a book's move tree from its variation documents.

    Returns the root (``san is None``), whose ``children[0]`` is the mainline,
    as ``core.pgn.game_to_pgn`` expects.

    Documents are visited shallowest first, so a node's parent always exists by
    the time it is attached, whatever order the listing arrived in. A node whose
    parent is genuinely absent raises, and so does a path that appears twice:
    either way the listing is not the tree it claims to be.

    A document's ``comment`` and ``nags`` ride along onto the node it names; the
    prose describes the position the move reaches, so it is the node's
    ``post_comment``. The root document's comment is the book's own preface
    rather than an annotation, and is left on the returned root, which
    ``core.pgn`` never renders, for ``fetch`` to lift into the game comment.
    """
    parsed = [
        (_san_path(v.get("variation")), int(v.get("order") or 0), v) for v in variations
    ]
    # Depth first so parents precede children; then `order`, then the SAN
    # itself. Among siblings the path prefix is shared, so the tuple compares on
    # the last token and repeated `order` values still sort deterministically.
    # The document is not part of the key: dicts do not compare.
    parsed.sort(key=lambda p: (len(p[0]), p[1], p[0]))

    root = MoveNode(san=None)
    nodes: dict[tuple[str, ...], MoveNode] = {(): root}
    for path, _order, document in parsed:
        if not path:
            # The root document itself: no move to attach, but its prose is the
            # book's introduction and is kept.
            root.post_comment = _comment(document)
            continue
        if path in nodes:
            # Upstream keys each document by its own variation string, so this
            # should be impossible. Keeping the first would append the second as
            # a same-SAN sibling with everything deeper hanging off it, and the
            # rendered PGN would grow an empty twin branch that reads as a real
            # alternative line.
            raise FetchError(
                f"noctie variation {' '.join(path)!r} appears twice; "
                "the book listing is not a tree"
            )
        parent = nodes.get(path[:-1])
        if parent is None:
            raise FetchError(
                f"noctie variation {' '.join(path)!r} has no parent variation; "
                "the book listing is incomplete"
            )
        node = MoveNode(
            san=path[-1], post_comment=_comment(document), nags=_nags(document)
        )
        parent.children.append(node)
        nodes[path] = node
    return root


def slice_from(root: MoveNode, path: Sequence[str]) -> MoveNode | None:
    """A root playing ``path``, with the subtree found at ``path`` below it.

    ``None`` when the path is not in the tree. The moves leading to the node are
    replayed rather than the node's position being pinned with a ``FEN`` header,
    so a bookmark file opens ``1. e4 e5`` and continues: a game a reader can
    follow from the start, and one that concatenates with the book's other files.

    The subtree is linked in, not copied. Nothing mutates a built tree, and each
    of a book's items reads it concurrently.

    The replayed moves are copies, since they need children the book's own nodes
    do not have, but copies rather than blanks: whatever the book says about a
    move leading into the bookmark comes with it. A repertoire's opening
    paragraph commonly sits on the first move, one ply above every bookmark in
    the book, so rebuilding the prefix bare would drop it from every bookmark
    file while leaving the whole-book file correct.
    """
    node = root
    walked: list[MoveNode] = []
    for san in path:
        child = next((c for c in node.children if c.san == san), None)
        if child is None:
            return None
        node = child
        walked.append(child)
    if not path:
        return node  # the bookmark names the root, so the slice is the book

    sliced = MoveNode(san=None)
    parent = sliced
    for original in walked[:-1]:
        # Everything but the children, which are this slice's own. `replace`
        # rather than a listed-out copy, so a field added to MoveNode later is
        # carried across instead of silently dropped; and `deepcopy` over the
        # result, because `replace` copies field references, so the two nodes
        # would otherwise hold one `nags`/`arrows`/`highlights` list between
        # them. The children are already emptied, so nothing deep is copied.
        step = deepcopy(replace(original, children=[]))
        parent.children.append(step)
        parent = step
    parent.children.append(node)
    return sliced


class OpeningBooksExtractor(NoctieExtractor):
    """One noctie opening book: the whole tree as one game, plus its bookmarks."""

    key = "noctie:opening-books"
    url_patterns = (re.compile(r"^https?://(app\.)?noctie\.ai/opening-books?/"),)

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.book_id = book_id_from(target)
        self._name = ""
        self._color = ""
        self._description = ""
        self._full_description = ""
        # Not list[dict]: an entry upstream stored as something other than a map
        # is kept, and fails its own item in fetch().
        self._bookmarks: list[Any] = []
        self._tree: MoveNode | None = None
        self._tree_lock = asyncio.Lock()

    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        book = await self.read(
            lambda token: get_document(fetcher, f"openingBooks/{self.book_id}", token),
            _NOUN,
        )
        self._name = book.get("name") or self.book_id
        self._color = book.get("color") or ""
        # Prose, not identity: these go in the game comment, never a header. A
        # `fullDescription` is multi-line markdown and a PGN header is one line,
        # so a header would either truncate it or produce a file readers reject.
        self._description = str(book.get("description") or "")
        self._full_description = str(book.get("fullDescription") or "")
        # Nothing here may raise on a malformed bookmark: the whole-book item
        # needs none of them, and discover() failing loses it too. A `bookmarks`
        # that is not an array has no entries to make items from, so it reads as
        # a book with none.
        bookmarks = book.get("bookmarks")
        self._bookmarks = bookmarks if isinstance(bookmarks, list) else []
        collection = self.collection(
            # Book names are not unique upstream, so two different books would
            # otherwise share an output directory and overwrite each other.
            slug=f"{slugify(self._name)}-{self.book_id[:8]}",
            name=self._name,
            # Unlike themes, this source emits a move tree, so the runner
            # renders the PGN itself and these headers do reach the file.
            headers={"Event": self._name, "Site": SITE},
        )
        # The variations are the book's moves, not its items: the whole book is
        # one item, and each bookmark another cut of that same tree. A book
        # without bookmarks stays a single file with no `bookmarks/` beside it.
        items = [ItemRef(id=self.book_id, path=(_book_file_slug(self._name),))]
        items += [
            ItemRef(
                id=f"{BOOKMARK_ID}{i}",
                path=("bookmarks", f"{i:02d}-{slugify(_bookmark_name(b))}"),
            )
            for i, b in enumerate(self._bookmarks, start=1)
        ]
        return collection, items

    async def _book_tree(self, fetcher: Fetcher) -> MoveNode:
        """The book's move tree, listed and built at most once per run.

        Every item is a view of this one tree, so listing per item would repeat
        a paginated read of hundreds of documents once per bookmark. ``fetch``
        calls run concurrently under the runner's semaphore, so the lock is what
        stops two of them starting the listing at the same moment.

        A failed listing is deliberately not cached: a bot challenge is retried
        by the runner, and caching the failure would turn one bad moment into a
        whole run's worth of failed items.
        """
        async with self._tree_lock:
            if self._tree is None:
                # No page cap: a capped listing silently drops whole branches.
                variations = await self.read(
                    lambda token: list_documents(
                        fetcher,
                        f"openingBooks/{self.book_id}/openingBookVariations",
                        token,
                    ),
                    _NOUN,
                )
                if not variations:
                    # An empty tree would have the runner write a moveless file
                    # and then skip it on every later run.
                    raise FetchError(
                        f"noctie opening book {self.book_id} has no variations"
                    )
                self._tree = build_tree(variations)
            return self._tree

    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        tree = await self._book_tree(fetcher)
        headers = {"NoctieBookId": self.book_id}
        # The whole book opens with everything the book says about itself: its
        # blurb, its long description, and the preface on the root document. A
        # bookmark is a cut of the same tree and gets its own description
        # instead, rather than repeating the introduction in every file.
        comment = _prose(self._description, self._full_description, tree.post_comment)
        root = tree

        if ref.id.startswith(BOOKMARK_ID):
            position = int(ref.id.removeprefix(BOOKMARK_ID))
            bookmark = self._bookmarks[position - 1]
            if not isinstance(bookmark, dict):
                # Kept as an item and failed here rather than dropped in
                # discover(), so the run's tally says a bookmark was not
                # written. Dropping it would lose a file silently.
                raise FetchError(
                    f"noctie bookmark {position} of book {self.book_id} is a "
                    f"{type(bookmark).__name__}, not a map"
                )
            name = _bookmark_name(bookmark)
            path = _san_path(bookmark.get("variation"))
            sliced = slice_from(tree, path)
            if sliced is None:
                # As with an orphan variation: writing the prefix and nothing
                # else would be a short game the runner tallies as downloaded
                # and every later run skips.
                raise FetchError(
                    f"noctie bookmark {name!r} points at variation "
                    f"{' '.join(path)!r}, which is not in book {self.book_id}"
                )
            root = sliced
            comment = str(bookmark.get("description") or "")
            # Readers key on Event, so that names the bookmark; the book it was
            # cut from keeps its own headers so the file still says where it
            # came from.
            headers |= {"Event": name, "NoctieBook": self._name}

        # A book is written from one side's point of view; naming that side
        # tells a reader whose repertoire this is at a glance.
        side = _REPERTOIRE_SIDE.get(self._color.lower())
        if side is not None:
            headers |= {"White": "?", "Black": "?", side: self._name}
        return GameItem(headers=headers, comment=comment, root=root)
