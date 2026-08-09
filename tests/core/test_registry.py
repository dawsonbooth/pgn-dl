"""Extractor registration and URL resolution."""

from __future__ import annotations

import re

import pytest
import typer

from pgn_dl.core import registry
from pgn_dl.core.extractor import Extractor
from pgn_dl.core.models import Collection, GameItem, ItemRef
from pgn_dl.core.registry import (
    DuplicateSource,
    UnknownSource,
    resolve_key,
    resolve_url,
)


class AlphaExtractor(Extractor):
    key = "alpha:things"
    url_patterns = (re.compile(r"^https://alpha\.test/things/(?P<id>\w+)"),)
    needs_browser = False

    def __init__(self, target: str) -> None:
        self.target = target

    @classmethod
    def from_url(cls, url: str) -> AlphaExtractor | None:
        for pattern in cls.url_patterns:
            if m := pattern.search(url):
                return cls(m.group("id"))
        return None

    async def discover(self, fetcher: object) -> tuple[Collection, list[ItemRef]]:
        return Collection(source="alpha", kind="things", slug="s", name="S"), []

    async def fetch(self, fetcher: object, ref: ItemRef) -> GameItem:
        return GameItem()


class BetaExtractor(AlphaExtractor):
    key = "beta:things"
    url_patterns = (re.compile(r"^https://beta\.test/(?P<id>\w+)"),)


def test_subclassing_registers_the_extractor() -> None:
    assert resolve_key("alpha:things") is AlphaExtractor
    assert resolve_key("beta:things") is BetaExtractor


def test_key_splits_into_source_and_kind() -> None:
    assert AlphaExtractor.source == "alpha"
    assert AlphaExtractor.kind == "things"


def test_resolve_url_picks_the_owning_extractor() -> None:
    got = resolve_url("https://alpha.test/things/abc123")
    assert isinstance(got, AlphaExtractor)
    assert got.target == "abc123"
    assert isinstance(resolve_url("https://beta.test/xyz"), BetaExtractor)


def test_resolve_url_rejects_unknown_urls() -> None:
    with pytest.raises(UnknownSource):
        resolve_url("https://example.com/nope")


def test_resolve_key_rejects_unknown_keys() -> None:
    with pytest.raises(UnknownSource):
        resolve_key("gamma:things")


def test_a_duplicate_key_is_rejected_rather_than_shadowing() -> None:
    # A copy-pasted source directory with an un-edited key would otherwise take
    # over the original's key, and its output directory, silently.
    with pytest.raises(DuplicateSource):

        class Clone(AlphaExtractor):
            key = "alpha:things"

    assert resolve_key("alpha:things") is AlphaExtractor


def test_a_duplicate_sub_app_is_rejected_rather_than_shadowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A copy-pasted source that kept register_app("noctie", ...) would take the
    # original's place in --help, and nothing would say so.
    monkeypatch.setattr(registry, "_APPS", {})
    app = typer.Typer()
    registry.register_app("alpha", app)
    with pytest.raises(DuplicateSource):
        registry.register_app("alpha", typer.Typer())
    assert registry.apps()["alpha"] is app


def test_a_duplicate_login_is_rejected_rather_than_shadowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same failure mode as a duplicate sub-app: a copy-pasted source keeping the
    # original's login name would silently steal `pgn-dl login <name>`.
    async def sign_in() -> None:  # pragma: no cover - never invoked
        raise AssertionError

    monkeypatch.setattr(registry, "_LOGINS", {})
    registry.register_login("alpha", sign_in)
    with pytest.raises(DuplicateSource):
        registry.register_login("alpha", sign_in)
    assert registry.logins()["alpha"] is sign_in


def test_a_key_without_a_kind_is_rejected() -> None:
    with pytest.raises(ValueError):

        class Malformed(AlphaExtractor):
            key = "delta"


def test_the_collection_helper_takes_its_identity_from_the_key() -> None:
    collection = AlphaExtractor.collection(slug="s", name="S")
    assert (collection.source, collection.kind) == ("alpha", "things")
