"""chessreps.com's source declaration: the commands and login it announces."""

from __future__ import annotations

from pgn_dl.sources import chessreps
from pgn_dl.sources.chessreps.auth import login
from pgn_dl.sources.chessreps.openings import OpeningsExtractor


def test_the_declaration_names_the_site_and_login() -> None:
    assert chessreps.declaration.label == "chessreps.com"
    assert chessreps.declaration.login is login


def test_the_declaration_covers_every_extractor_under_its_own_target_name() -> None:
    by_kind = {e.extractor.kind: e for e in chessreps.declaration.extractors}
    assert by_kind.keys() == {"openings"}
    assert by_kind["openings"].extractor is OpeningsExtractor
    assert by_kind["openings"].target_name == "opening"
