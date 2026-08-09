"""Access failures shared by both noctie sources.

The behavior under test lives once, in ``NoctieExtractor.read`` and
``firestore.guarded_read``, so each case runs against both extractors: what
matters is that every source wires its discovery reads through the guard.
"""

from __future__ import annotations

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.noctie import auth
from pgn_dl.sources.noctie.books import OpeningBooksExtractor
from pgn_dl.sources.noctie.themes import ThemesExtractor

from .conftest import failing_fetcher

pytestmark = pytest.mark.usefixtures("no_credentials")

#: An id containing "403", for the case that a status must never be read out
#: of message prose: about one UUID in a hundred contains that substring.
ID_WITH_403 = "40312f2a-1b2c-4d3e-8f90-abcdef123456"

EXTRACTORS = [
    pytest.param(ThemesExtractor, "https://app.noctie.ai/themes/{}", id="themes"),
    pytest.param(
        OpeningBooksExtractor, "https://app.noctie.ai/opening-book/{}", id="books"
    ),
]


@pytest.mark.parametrize(("extractor_cls", "url"), EXTRACTORS)
async def test_a_paid_collection_says_so_rather_than_looking_broken(
    extractor_cls, url
) -> None:
    # The collection document read is itself what 403s, so requiresTier cannot
    # be read first and branched on; the message has to be built from the 403.
    fetcher = failing_fetcher(403, "Missing or insufficient permissions.")
    with pytest.raises(FetchError) as exc:
        await extractor_cls(url.format(ID_WITH_403)).discover(fetcher)
    message = str(exc.value)
    assert "403" in message  # the original status survives
    assert "tier" in message
    assert "noctie" in message


@pytest.mark.parametrize(("extractor_cls", "url"), EXTRACTORS)
async def test_a_non_403_failure_is_not_dressed_up_as_a_paywall(
    extractor_cls, url
) -> None:
    with pytest.raises(FetchError) as exc:
        await extractor_cls(url.format(ID_WITH_403)).discover(
            failing_fetcher(404, "Not found.")
        )
    assert "tier" not in str(exc.value)


@pytest.mark.parametrize(("extractor_cls", "url"), EXTRACTORS)
async def test_an_id_containing_403_does_not_fake_a_paywall(
    extractor_cls, url
) -> None:
    # The failing URL is embedded in the error message, so the typed status is
    # what gets matched, never the text.
    assert "403" in ID_WITH_403
    with pytest.raises(FetchError) as exc:
        await extractor_cls(url.format(ID_WITH_403)).discover(
            failing_fetcher(500, "upstream error")
        )
    assert "tier" not in str(exc.value)


@pytest.mark.parametrize(("extractor_cls", "url"), EXTRACTORS)
async def test_a_missing_login_surfaces_as_an_actionable_error(
    extractor_cls, url, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_credentials() -> str:
        raise FetchError("no saved noctie credentials; run: pgn-dl login noctie")

    monkeypatch.setattr(auth, "id_token", no_credentials)
    with pytest.raises(FetchError) as exc:
        await extractor_cls(url.format(ID_WITH_403)).discover(failing_fetcher(500, "x"))
    assert "pgn-dl login noctie" in str(exc.value)
