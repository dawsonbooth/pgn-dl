"""noctie themes.

A theme is a numbered sequence of elements (flashcards, sparring positions,
instruction pages) and each element's ``pgn`` field is already a complete PGN
string, so this source hands the text to the runner rather than rebuilding a
move tree.

Two upstream quirks show up in the code. The element listing comes back in no
particular order (a live listing gave indices 27, 13, 9, 40), so ``index`` has
to be sorted on before numbering. And theme names are not unique, so the output
slug carries a slice of the theme id as well.

The only rewriting done to an element's PGN is to its headers: upstream ``Event``
names the lichess study the material was imported from, which says nothing about
where this file came from. The lichess provenance headers (``Annotator``,
``ChapterName``, ``ChapterURL``, ...) are left alone; much of noctie's library
is imported from lichess studies and that attribution should survive.
"""

from __future__ import annotations

import re
from collections import Counter

from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from ...core.pgn import reexport_pgn
from .base import SITE, NoctieExtractor, id_from, target_pattern
from .firestore import get_document, list_documents

_TARGET_RE = target_pattern("themes?")

# What this source's material is called, in a rejected target and in the
# explanation of a denied read.
_NOUN = "theme"


def theme_id_from(target: str) -> str:
    """The theme id in a noctie themes URL, or a bare id."""
    return id_from(target, _TARGET_RE, _NOUN)


def element_to_pgn(pgn: str, headers: dict[str, str], element_id: str) -> str:
    """Re-export one element's PGN under our headers, keeping the rest.

    FetchError, not ValueError: an element whose PGN will not parse and an
    element with no pgn at all are the same outcome, and the runner should
    tally both the same way.
    """
    return reexport_pgn(
        pgn, headers, subject=f"noctie element {element_id}", error=FetchError
    )


class ThemesExtractor(NoctieExtractor):
    """One noctie theme, as one PGN file per element."""

    key = "noctie:themes"
    url_patterns = (re.compile(r"^https?://(app\.)?noctie\.ai/themes?/"),)

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.theme_id = theme_id_from(target)
        self._name = ""

    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        theme = await self.read(
            lambda token: get_document(fetcher, f"themes/{self.theme_id}", token), _NOUN
        )
        self._name = theme.get("name") or self.theme_id
        # The mask is not an optimisation detail: without it every element's
        # whole PGN is transferred here, and then again in fetch().
        elements = await self.read(
            lambda token: list_documents(
                fetcher,
                f"themes/{self.theme_id}/elements",
                token,
                mask=["index", "name", "type"],
            ),
            _NOUN,
        )
        # The theme document counts its own elements. A listing that returns
        # fewer is silent truncation: the theme downloads clean, one file short,
        # and every later run skips it as already done. Only that direction is
        # refused. numberOfElements is a denormalized counter, so a listing with
        # more in it than the count claims is just a stale count, and nothing is
        # lost. Compared only when the field is there; not every document
        # carries it.
        expected = theme.get("numberOfElements")
        if isinstance(expected, int) and len(elements) < expected:
            raise FetchError(
                f"noctie theme {self.theme_id} says it has {expected} elements "
                f"but the listing returned only {len(elements)}; that is either "
                "a truncated listing or a count gone stale upstream, and writing "
                "the part that arrived would have every later run skip the rest"
            )
        elements.sort(key=lambda e: e.get("index", 0))
        # Nothing upstream makes `index` unique, and an element carrying none
        # falls back to 0 along with every other that does, so two elements can
        # name one file. The runner refuses a shared path, which would cost the
        # theme both elements permanently, so a slice of the element id (unique
        # by construction) separates them instead. Every member of a clashing
        # group is suffixed, so which one gets the plain name does not depend on
        # the listing order.
        names = [
            f"{e.get('index', 0):03d}-{slugify(e.get('name', ''))}" for e in elements
        ]
        clashing = {name for name, n in Counter(names).items() if n > 1}
        refs = [
            ItemRef(
                id=e["_id"],
                path=((f"{name}-{e['_id'][:8]}" if name in clashing else name),),
            )
            for name, e in zip(names, elements, strict=True)
        ]
        # No headers: this source fills GameItem.pgn, which the runner writes
        # verbatim, so Collection.headers would never reach a file. fetch()
        # writes the headers that matter into the PGN itself.
        collection = self.collection(
            # Names are not unique upstream, so two different themes would
            # otherwise share an output directory and overwrite each other.
            slug=f"{slugify(self._name)}-{self.theme_id[:8]}",
            name=self._name,
        )
        return collection, refs

    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        # Guarded like the reads in discover(): the theme document can be
        # readable while an element is not, and an unexplained 403 mid-download
        # looks like a broken tool just the same.
        element = await self.read(
            lambda token: get_document(
                fetcher, f"themes/{self.theme_id}/elements/{ref.id}", token
            ),
            _NOUN,
        )
        pgn = element.get("pgn")
        if not pgn:
            # Returning nothing would have the runner write an empty file and
            # then skip it on every later run.
            raise FetchError(f"noctie element {ref.id} carries no pgn")
        headers = {"Event": self._name, "Site": SITE}
        headers["ElementName"] = element.get("name", "")
        # instruction / flashcard / scenario are genuinely different artifacts;
        # recording which keeps a downloaded theme filterable by kind.
        headers["ElementType"] = element.get("type", "")
        headers["NoctieElementId"] = ref.id
        return GameItem(pgn=element_to_pgn(pgn, headers, ref.id))
