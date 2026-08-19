"""Extractor registration and URL resolution."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import pytest

from pgn_dl.core import registry
from pgn_dl.core.declaration import ExtractorDeclaration, SourceDeclaration
from pgn_dl.core.extractor import Extractor
from pgn_dl.core.models import Collection, GameItem, ItemRef
from pgn_dl.core.registry import (
    DuplicateSource,
    UnknownSource,
    resolve_key,
    resolve_url,
)


class AlphaExtractor(Extractor[None]):
    key = "alpha:things"
    url_patterns = (re.compile(r"^https://alpha\.test/things/(?P<id>\w+)"),)
    needs_browser = False

    def __init__(self, target: str) -> None:
        super().__init__()
        self.target = target

    @classmethod
    def from_url(cls, url: str) -> AlphaExtractor | None:
        for pattern in cls.url_patterns:
            if m := pattern.search(url):
                return cls(m.group("id"))
        return None

    async def _discover(
        self, fetcher: object
    ) -> tuple[Collection, list[ItemRef], None]:
        return Collection(source="alpha", kind="things", slug="s", name="S"), [], None

    async def _fetch(self, context: None, fetcher: object, ref: ItemRef) -> GameItem:
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


def _declaration(
    *, label: str, login: Callable[[], Awaitable[None]] | None = None
) -> SourceDeclaration:
    return SourceDeclaration(
        label=label,
        login=login,
        extractors=(
            ExtractorDeclaration(
                extractor=AlphaExtractor,
                help="Download alpha things.",
                target_name="thing",
                target_help="A thing id.",
            ),
        ),
    )


def test_a_declaration_is_recorded_under_its_extractors_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "_DECLARATIONS", {})
    declaration = _declaration(label="Alpha")
    registry.register_declaration(declaration)
    assert registry.declarations()["alpha"] is declaration


def test_a_duplicate_declaration_is_rejected_rather_than_shadowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A copy-pasted source that kept register_declaration(declaration) with the
    # original's extractors would take the original's place on the command
    # line and in `pgn-dl login`, and nothing would say so.
    monkeypatch.setattr(registry, "_DECLARATIONS", {})
    first = _declaration(label="Alpha")
    registry.register_declaration(first)
    with pytest.raises(DuplicateSource):
        registry.register_declaration(_declaration(label="Alpha Clone"))
    assert registry.declarations()["alpha"] is first


def test_has_login_reflects_the_registered_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sign_in() -> None:  # pragma: no cover - never invoked
        raise AssertionError

    monkeypatch.setattr(registry, "_DECLARATIONS", {})
    assert registry.has_login("alpha") is False
    registry.register_declaration(_declaration(label="Alpha", login=sign_in))
    assert registry.has_login("alpha") is True


def test_a_key_without_a_kind_is_rejected() -> None:
    with pytest.raises(ValueError):

        class Malformed(AlphaExtractor):
            key = "delta"


def test_the_collection_helper_takes_its_identity_from_the_key() -> None:
    collection = AlphaExtractor.collection(slug="s", name="S")
    assert (collection.source, collection.kind) == ("alpha", "things")
