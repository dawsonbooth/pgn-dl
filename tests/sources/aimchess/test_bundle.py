"""Finding content in the site's scripts, and reading JavaScript strings."""

from __future__ import annotations

import re

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.aimchess.bundle import STRING, find_script, string_value

from .conftest import APP_SCRIPT, ASSETS, QUIZ_CHUNK, SiteFetcher, site_pages


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (r"'don\'t\nstop'", "don't\nstop"),
        (r'"say \"hi\" \\ bye"', 'say "hi" \\ bye'),
        (r"'\uFEFF[Event \x41]'", "\ufeff[Event A]"),
    ],
)
def test_string_value(literal: str, expected: str) -> None:
    match = re.fullmatch(STRING, literal)
    assert match is not None
    assert string_value(match, 1) == expected


async def test_content_in_an_entry_script_needs_no_chunks() -> None:
    fetcher = SiteFetcher()
    assert await find_script(fetcher, "/motif/fork", "fork:{name:") == APP_SCRIPT
    assert not any("bbbb2222" in url for url in fetcher.gets)


async def test_chunks_are_searched_in_map_order() -> None:
    fetcher = SiteFetcher()
    assert await find_script(fetcher, "/public/quiz", "puzzles:[") == QUIZ_CHUNK
    assert fetcher.gets[-2:] == [
        f"{ASSETS}/12.aaaa1111.bundle.js",
        f"{ASSETS}/34.bbbb2222.bundle.js",
    ]
    assert not any("googletagmanager" in url for url in fetcher.gets)


async def test_missing_content_fails() -> None:
    with pytest.raises(FetchError, match="no aimchess script contains"):
        await find_script(SiteFetcher(), "/public/quiz", "nowhere:[")
    no_map = site_pages(**{f"{ASSETS}/app.bundle.js": "var a=1;"})
    with pytest.raises(FetchError, match="no chunk map"):
        await find_script(SiteFetcher(no_map), "/public/quiz", "nowhere:[")
