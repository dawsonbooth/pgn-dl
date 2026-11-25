"""lichess.org's source declaration."""

from __future__ import annotations

from pgn_dl.core.registry import resolve_url
from pgn_dl.sources import lichess
from pgn_dl.sources.lichess.auth import login


def test_the_declaration_names_the_site_and_login() -> None:
    assert lichess.declaration.label == "lichess.org"
    assert lichess.declaration.login is login


def test_every_extractor_is_declared() -> None:
    kinds = [e.extractor.kind for e in lichess.declaration.extractors]
    assert kinds == ["learn", "practice", "study", "user-studies", "openings"]


def test_the_help_says_which_kinds_need_an_account() -> None:
    by_kind = {e.extractor.kind: e.help for e in lichess.declaration.extractors}
    # Words, not sentences: the help is prose that gets reflowed.
    assert "No login needed" in by_kind["learn"]
    assert "No login needed" in by_kind["practice"]
    assert "needs `pgn-dl login lichess`" in by_kind["study"]
    assert "logged in as that user" in by_kind["user-studies"]
    assert "requires" in by_kind["openings"]
    assert "Chrome window" in by_kind["openings"]


def test_each_kind_owns_its_urls() -> None:
    owners = {
        "https://lichess.org/learn": "lichess:learn",
        "https://lichess.org/learn#/3": "lichess:learn",
        "https://lichess.org/practice": "lichess:practice",
        "https://lichess.org/practice/checkmates/piece-checkmates-i/BJy6fEDf": (
            "lichess:practice"
        ),
        "https://lichess.org/study/LAV8k5kM": "lichess:study",
        "https://lichess.org/study/fE4k21MW/9rd7XwOw": "lichess:study",
        "https://lichess.org/study/by/thibault": "lichess:user-studies",
        "https://lichess.org/study/by/thibault/hot": "lichess:user-studies",
        "https://lichess.org/opening": "lichess:openings",
        "https://lichess.org/opening/London_System": "lichess:openings",
    }
    for url, key in owners.items():
        assert resolve_url(url).key == key, url
