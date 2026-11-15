"""aimchess.com's source declaration."""

from __future__ import annotations

from pgn_dl.sources import aimchess
from pgn_dl.sources.aimchess.auth import login


def test_the_declaration_names_the_site_and_login() -> None:
    assert aimchess.declaration.label == "aimchess.com"
    assert aimchess.declaration.login is login


def test_every_extractor_is_declared() -> None:
    kinds = [e.extractor.kind for e in aimchess.declaration.extractors]
    assert kinds == ["explore", "checkmate-patterns", "quiz", "motifs"]


def test_the_explore_help_says_downloading_marks_lessons_started() -> None:
    explore = aimchess.declaration.extractors[0]
    assert "marks it as started" in explore.help
