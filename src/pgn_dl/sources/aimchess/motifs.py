"""aimchess's tactical motifs.

The motif pages (/motif/fork, /motif/pin, ...) step through 20 motifs, each a
definition and an annotated example. All of them are compiled into the site's
JavaScript, so no login is needed. A motif's URL downloads that motif as one
file, numbered in the site's order. Some examples open with the mistake that
allows the tactic and others with the tactic itself, so no side is named as the
player.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError, UnknownTarget
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from .bundle import STRING, find_script, string_value
from .pgn import SITE, rewrite

COLLECTION_NAME = "Tactical Motifs"

_URL = re.compile(
    r"^https?://(www\.)?aimchess\.com/motif/\w+(?:[/?#]|$)", re.IGNORECASE
)
#: ``_URL`` with the motif's key captured. ``url_patterns`` keeps the plain
#: ``_URL`` because ``pgn-dl sources`` prints it.
_TARGET_RE = re.compile(
    r"^https?://(www\.)?aimchess\.com/motif/(?P<key>\w+)(?:[/?#]|$)", re.IGNORECASE
)

#: Where the motif table starts in its script.
_MARKER = "fork:{name:"
_MOTIF = re.compile(
    r"(\w+):\{name:" + STRING + r",desc:" + STRING + r",pgn:" + STRING + r"\}"
)


@dataclass(frozen=True)
class Motif:
    key: str  # the id in the motif's URL
    name: str
    definition: str
    pgn: str


def parse_motifs(script: str) -> list[Motif]:
    """The motif table's entries, which follow one another separated by commas."""
    position = script.find(_MARKER)
    motifs = []
    while position >= 0 and (m := _MOTIF.match(script, position)) is not None:
        name = string_value(m, 2).strip()
        motifs.append(Motif(m.group(1), name, string_value(m, 4), string_value(m, 6)))
        position = m.end() + 1
    if not motifs:
        raise FetchError("found no motifs in aimchess's scripts")
    return motifs


def motif_from(target: str) -> str:
    """The motif a motif URL names, as its key in the motif table."""
    m = _TARGET_RE.match(target.strip())
    if m is None:
        raise ValueError(f"not an aimchess motif URL: {target!r}")
    return m.group("key")


class MotifsExtractor(Extractor[dict[str, Motif]]):
    """The tactical motif a URL names."""

    key = "aimchess:motifs"
    url_patterns = (_URL,)

    def __init__(self, target: str) -> None:
        super().__init__()
        self.motif = motif_from(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], dict[str, Motif]]:
        motifs = parse_motifs(await find_script(fetcher, "/motif/fork", _MARKER))
        # Numbered before filtering, so the number is the motif's place in the
        # whole table.
        refs = [
            ItemRef(id=motif.key, path=(f"{n:02d}-{slugify(motif.name, 'motif')}",))
            for n, motif in enumerate(motifs, start=1)
            if motif.key.lower() == self.motif.lower()
        ]
        if not refs:
            raise UnknownTarget(f"aimchess has no motif {self.motif!r}")
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        return collection, refs, {motif.key: motif for motif in motifs}

    async def _fetch(
        self, context: dict[str, Motif], fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        motif = context[ref.id]
        headers = {"Event": motif.name, "Site": SITE, "AimchessMotifId": motif.key}
        return GameItem(
            pgn=rewrite(
                motif.pgn,
                headers,
                subject=f"aimchess motif {motif.key}",
                preface=motif.definition,
            )
        )
