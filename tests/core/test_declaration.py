"""SourceDeclaration's own invariants: it names exactly one source."""

from __future__ import annotations

import re

import pytest

from pgn_dl.core.declaration import ExtractorDeclaration, SourceDeclaration
from pgn_dl.core.extractor import Extractor
from pgn_dl.core.models import Collection, GameItem, ItemRef


class _FirstExtractor(Extractor):
    key = "firstsource:things"

    def __init__(self, target: str) -> None:
        self.target = target

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        return self.collection(slug="s", name="S"), []

    async def fetch(self, fetcher: object, ref: ItemRef) -> GameItem:
        return GameItem()


class _SecondExtractor(_FirstExtractor):
    key = "secondsource:things"


def _entry(extractor: type[Extractor]) -> ExtractorDeclaration:
    return ExtractorDeclaration(
        extractor=extractor, help="unused", target_name="target", target_help="unused"
    )


def test_a_declaration_with_no_extractors_is_rejected() -> None:
    with pytest.raises(ValueError, match="declares no extractors"):
        SourceDeclaration(label="empty", extractors=())


def test_a_declaration_mixing_two_sources_extractors_is_rejected() -> None:
    with pytest.raises(ValueError, match=re.escape("['firstsource', 'secondsource']")):
        SourceDeclaration(
            label="mixed",
            extractors=(_entry(_FirstExtractor), _entry(_SecondExtractor)),
        )


def test_source_is_derived_from_the_shared_extractor_source() -> None:
    declaration = SourceDeclaration(label="ok", extractors=(_entry(_FirstExtractor),))
    assert declaration.source == "firstsource"
