"""Shared fakes for the noctie source tests.

test_firestore.py keeps a fetcher of its own: its paging tests address pages by
full URL including the query string, which this one deliberately strips.
"""

from __future__ import annotations

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.noctie import auth


class FakeFetcher:
    """Serves the fixtures by URL path, recording every URL asked for."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages  # keyed by URL without its query string
        self.gets: list[str] = []

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("firestore reads must be GETs")

    async def get_text(self, url, *, headers):
        self.gets.append(url)
        assert headers.get("authorization") == "Bearer tok", "must send the ID token"
        try:
            return self.pages[url.split("?", 1)[0]]
        except KeyError:
            raise FetchError(f"{url} failed: 404", status=404) from None


def failing_fetcher(status: int, detail: str) -> FakeFetcher:
    """A fetcher whose every read fails with the one typed status given."""

    class Failing(FakeFetcher):
        async def get_text(self, url, *, headers):
            self.gets.append(url)
            raise FetchError(f"{url} failed: {status} {detail}", status=status)

    return Failing({})


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read the credential file or call Google.

    Opt-in per module (``pytestmark = pytest.mark.usefixtures(...)``) rather
    than autouse: test_auth exercises the real ``auth.id_token``.
    """

    async def fake_id_token() -> str:
        return "tok"

    monkeypatch.setattr(auth, "id_token", fake_id_token)
