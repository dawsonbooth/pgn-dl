"""chess.com's source declaration: the commands and login it announces."""

from __future__ import annotations

from pgn_dl.sources import chesscom
from pgn_dl.sources.chesscom.auth import login
from pgn_dl.sources.chesscom.courses import CoursesExtractor
from pgn_dl.sources.chesscom.lessons import LessonsExtractor


def test_the_declaration_names_the_site_and_login() -> None:
    assert chesscom.declaration.label == "chess.com"
    assert chesscom.declaration.login is login


def test_the_declaration_covers_every_extractor_under_its_own_target_name() -> None:
    by_kind = {e.extractor.kind: e for e in chesscom.declaration.extractors}
    assert by_kind.keys() == {"courses", "lessons"}
    assert by_kind["courses"].extractor is CoursesExtractor
    assert by_kind["courses"].target_name == "course"
    assert by_kind["lessons"].extractor is LessonsExtractor
    assert by_kind["lessons"].target_name == "target"
