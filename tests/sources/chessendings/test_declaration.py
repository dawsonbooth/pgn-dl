"""chessendings.com's source declaration."""

from __future__ import annotations

from pgn_dl.sources import chessendings
from pgn_dl.sources.chessendings.auth import login


def test_the_declaration_names_the_site_and_login() -> None:
    assert chessendings.declaration.label == "chessendings.com"
    assert chessendings.declaration.login is login


def test_every_extractor_is_declared() -> None:
    kinds = [e.extractor.kind for e in chessendings.declaration.extractors]
    assert kinds == ["theory", "strategy", "challenges", "guides"]


def test_the_help_says_which_sections_need_an_account() -> None:
    by_kind = {e.extractor.kind: e.help for e in chessendings.declaration.extractors}
    # Words, not sentences: the help is prose that gets reflowed.
    assert "free lessons need no account" in by_kind["theory"]
    assert "requires" in by_kind["strategy"]
    assert "`pgn-dl login chessendings`" in by_kind["strategy"]
    assert "No login needed" in by_kind["challenges"]
    assert "No login needed" in by_kind["guides"]
