"""Lichess Practice.

Practice is 32 studies in five sections, each chapter a position with a goal
(mate in 2, draw in 30, ...) that Lichess keeps in the chapter's
``Termination`` header. Which studies make up Practice, in what order and
under which names and one-line descriptions, is fixed in Lichess's source
(``modules/practice/src/main/PracticeSections.scala``). The practice page's
JSON lists the studies but not their descriptions, so the structure is
copied here, with the English strings from Lichess's
``translation/source/practice.xml``. It has not changed since 2018; a study
Lichess adds needs adding here.

Each study is one file, its chapters the file's games, in a directory per
section. They are written as ``study.py`` writes a study: the export, with
what it leaves out added as comments. For Practice that is mostly each
chapter's description, the instructions shown beside the board ("Use your
queen and rook to restrict the king and deliver checkmate"). The study's own
line from the practice page ("Basic checkmates") opens its first chapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from .client import Client
from .study import ID, whole_study

COLLECTION_NAME = "Practice"


@dataclass(frozen=True)
class PracticeStudy:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Section:
    id: str  # as in the practice URLs, /practice/<id>/<study slug>/<study id>
    name: str
    studies: tuple[PracticeStudy, ...]


SECTIONS = (
    Section(
        "checkmates",
        "Checkmates",
        (
            PracticeStudy("BJy6fEDf", "Piece Checkmates I", "Basic checkmates"),
            PracticeStudy("fE4k21MW", "Checkmate Patterns I", "Recognize the patterns"),
            PracticeStudy(
                "8yadFPpU", "Checkmate Patterns II", "Recognize the patterns"
            ),
            PracticeStudy(
                "PDkQDt6u", "Checkmate Patterns III", "Recognize the patterns"
            ),
            PracticeStudy(
                "96Lij7wH", "Checkmate Patterns IV", "Recognize the patterns"
            ),
            PracticeStudy("Rg2cMBZ6", "Piece Checkmates II", "Challenging checkmates"),
            PracticeStudy("ByhlXnmM", "Knight & Bishop Mate", "Interactive lesson"),
        ),
    ),
    Section(
        "fundamental-tactics",
        "Fundamental tactics",
        (
            PracticeStudy("9ogFv8Ac", "The Pin", "Pin it to win it"),
            PracticeStudy("tuoBxVE5", "The Skewer", "Yum - skewers!"),
            PracticeStudy("Qj281y1p", "The Fork", "Use the fork, Luke"),
            PracticeStudy(
                "MnsJEWnI", "Discovered Attacks", "Including discovered checks"
            ),
            PracticeStudy("RUQASaZm", "Double Check", "A very powerful tactic"),
            PracticeStudy("o734CNqp", "Overloaded Pieces", "They have too much work"),
            PracticeStudy("ITWY4GN2", "Zwischenzug", "In-between moves"),
            PracticeStudy("lyVYjhPG", "X-Ray", "Attacking through an enemy piece"),
        ),
    ),
    Section(
        "advanced-tactics",
        "Advanced tactics",
        (
            PracticeStudy("9cKgYrHb", "Zugzwang", "Being forced to move"),
            PracticeStudy(
                "g1fxVZu9", "Interference", "Interpose a piece to great effect"
            ),
            PracticeStudy("s5pLU7Of", "Greek Gift", "Study the greek gift sacrifice"),
            PracticeStudy("kdKpaYLW", "Deflection", "Distracting a defender"),
            PracticeStudy("jOZejFWk", "Attraction", "Lure a piece to a bad square"),
            PracticeStudy(
                "49fDW0wP", "Underpromotion", "Promote - but not to a queen!"
            ),
            PracticeStudy(
                "0YcGiH4Y", "Desperado", "A piece is lost, but it can still help"
            ),
            PracticeStudy(
                "CgjKPvxQ", "Counter Check", "Respond to a check with a check"
            ),
            PracticeStudy("udx042D6", "Undermining", "Remove the defending piece"),
            PracticeStudy("Grmtwuft", "Clearance", "Get out of the way!"),
        ),
    ),
    Section(
        "pawn-endgames",
        "Pawn Endgames",
        (
            PracticeStudy("xebrDvFe", "Key Squares", "Reach a key square"),
            PracticeStudy("A4ujYOer", "Opposition", "Take the opposition"),
            PracticeStudy("pt20yRkT", "7th-Rank Rook Pawn", "Versus a Queen"),
        ),
    ),
    Section(
        "rook-endgames",
        "Rook Endgames",
        (
            PracticeStudy("MkDViieT", "7th-Rank Rook Pawn", "And Passive Rook vs Rook"),
            PracticeStudy("pqUSUw8Y", "Basic Rook Endgames", "Lucena and Philidor"),
            PracticeStudy(
                "heQDnvq7", "Intermediate Rook Endings", "Broaden your knowledge"
            ),
            PracticeStudy(
                "wS23j5Tm", "Practical Rook Endings", "Rook endings with several pawns"
            ),
        ),
    ),
)

_STUDIES = {study.id: study for section in SECTIONS for study in section.studies}

#: The practice page, a section, a study or a chapter in it, or a study id.
_TARGET = re.compile(
    rf"^(?:https?://(?:www\.)?lichess\.org/practice"
    rf"(?:/(?P<section>[a-z-]+)(?:/[^/]+/(?P<study>{ID})(?:/{ID})?)?)?/?"
    rf"|(?P<bare>{ID}))$"
)


@dataclass(frozen=True)
class Scope:
    """Which studies a target names: every one, a section's, or one."""

    section: str | None = None
    study: str | None = None


def scope_from(target: str) -> Scope:
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET.match(cleaned)
    if m is None:
        raise ValueError(f"could not read lichess practice from {target!r}")
    scope = Scope(section=m.group("section"), study=m.group("study") or m.group("bare"))
    if scope.section is not None and scope.section not in {s.id for s in SECTIONS}:
        raise ValueError(f"lichess practice has no section {scope.section!r}")
    if scope.study is not None and scope.study not in _STUDIES:
        raise ValueError(f"{scope.study!r} is not a lichess practice study")
    return scope


def layout() -> list[tuple[PracticeStudy, tuple[str, str]]]:
    """Every study with its path: section directory, then study, both numbered."""
    return [
        (study, (f"{n:02d}-{section.id}", f"{m:02d}-{slugify(study.name)}"))
        for n, section in enumerate(SECTIONS, start=1)
        for m, study in enumerate(section.studies, start=1)
    ]


class PracticeExtractor(Extractor[Client]):
    """Every practice study, a section's, or one."""

    key = "lichess:practice"
    url_patterns = (
        re.compile(r"^https?://(?:www\.)?lichess\.org/practice(?:[/?#]|$)"),
    )

    def __init__(self, target: str) -> None:
        super().__init__()
        self.scope = scope_from(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], Client]:
        sections = {
            study.id: section.id for section in SECTIONS for study in section.studies
        }
        refs = [
            ItemRef(id=study.id, path=path)
            for study, path in layout()
            if self.scope.section in (None, sections[study.id])
            and self.scope.study in (None, study.id)
        ]
        if not refs:
            raise FetchError(
                f"lichess practice study {self.scope.study} is not in section "
                f"{self.scope.section}"
            )
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        # Practice studies are public: no token is sent, so a stale one cannot
        # get in the way.
        return collection, refs, Client(fetcher)

    async def _fetch(self, context: Client, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        study = _STUDIES[ref.id]
        text = await whole_study(
            context, ref.id, intro=(study.description,), send_token=False
        )
        return GameItem(pgn=text)
