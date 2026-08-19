"""noctie.ai's source declaration: the commands and login it announces."""

from __future__ import annotations

from pgn_dl.sources import noctie
from pgn_dl.sources.noctie.auth import login
from pgn_dl.sources.noctie.books import OpeningBooksExtractor
from pgn_dl.sources.noctie.themes import ThemesExtractor


def test_the_declaration_names_the_site_and_login() -> None:
    assert noctie.declaration.label == "noctie.ai"
    assert noctie.declaration.login is login


def test_the_declaration_covers_every_extractor_under_its_own_target_name() -> None:
    by_kind = {e.extractor.kind: e for e in noctie.declaration.extractors}
    assert by_kind.keys() == {"themes", "opening-books"}
    assert by_kind["themes"].extractor is ThemesExtractor
    assert by_kind["themes"].target_name == "theme"
    assert by_kind["opening-books"].extractor is OpeningBooksExtractor
    assert by_kind["opening-books"].target_name == "book"
