"""chessable.com's source declaration: the commands and login it announces."""

from __future__ import annotations

from pgn_dl.sources import chessable
from pgn_dl.sources.chessable.auth import login
from pgn_dl.sources.chessable.courses import CoursesExtractor


def test_the_declaration_names_the_site_and_login() -> None:
    assert chessable.declaration.label == "chessable.com"
    assert chessable.declaration.login is login


def test_the_declaration_covers_every_extractor_under_its_own_target_name() -> None:
    by_kind = {e.extractor.kind: e for e in chessable.declaration.extractors}
    assert by_kind.keys() == {"courses"}
    assert by_kind["courses"].extractor is CoursesExtractor
    assert by_kind["courses"].target_name == "course"
