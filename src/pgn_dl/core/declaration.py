"""Source declarations: the pure data a source package announces itself with.

Extractors register themselves at class-definition time (``extractor.py``); a
declaration supplies everything else the command line needs and nothing
behavioral - site label, login flow, and one entry per extractor carrying the
command's help prose and the target argument's display name.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .extractor import Extractor


@dataclass(frozen=True)
class ExtractorDeclaration:
    """One extractor's command-line presentation.

    The command name is not declared here: it is the extractor's own
    ``kind``, which already names the registry key and output directory.
    """

    extractor: type[Extractor]
    help: str
    target_name: str
    target_help: str


@dataclass(frozen=True)
class SourceDeclaration:
    """A source package's presentation: its label, login, and its extractors."""

    label: str
    extractors: tuple[ExtractorDeclaration, ...]
    login: Callable[[], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if not self.extractors:
            raise ValueError(f"{self.label!r} declares no extractors")
        sources = {entry.extractor.source for entry in self.extractors}
        if len(sources) > 1:
            raise ValueError(
                f"{self.label!r} mixes extractors from sources {sorted(sources)}; "
                "a declaration belongs to exactly one source"
            )

    @property
    def source(self) -> str:
        """The source name every extractor here shares, e.g. "chesscom"."""
        return self.extractors[0].extractor.source
