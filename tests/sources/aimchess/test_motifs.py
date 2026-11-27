"""Tactical motifs: target, the motif table, one file per motif."""

from __future__ import annotations

import io

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError, UnknownTarget
from pgn_dl.sources.aimchess.motifs import MotifsExtractor, parse_motifs

from .conftest import APP_SCRIPT, SiteFetcher


def test_a_motif_url_resolves_to_this_extractor() -> None:
    found = registry.resolve_url("https://aimchess.com/motif/pin")
    assert isinstance(found, MotifsExtractor) and found.motif == "pin"
    with pytest.raises(ValueError):
        MotifsExtractor("https://aimchess.com/motif/")


def test_the_table_is_read_entry_by_entry() -> None:
    motifs = parse_motifs(APP_SCRIPT)
    assert [(m.key, m.name) for m in motifs] == [
        ("fork", "Fork"),
        ("doubleCheck", "Doble"),
    ]
    assert motifs[0].definition == "Two attacks at once."
    with pytest.raises(FetchError):
        parse_motifs("var m={};")


@pytest.mark.parametrize("key", ["doubleCheck", "doublecheck"])
async def test_a_motif_keeps_its_number_in_the_site_s_order(key: str) -> None:
    extractor = MotifsExtractor(f"https://aimchess.com/motif/{key}")
    collection, refs = await extractor.discover(SiteFetcher())
    assert (collection.kind, collection.slug) == ("motifs", "tactical-motifs")
    assert [(r.id, r.path) for r in refs] == [("doubleCheck", ("02-doble",))]


async def test_a_motif_the_table_lacks_fails_before_anything_is_listed() -> None:
    extractor = MotifsExtractor("https://aimchess.com/motif/nonesuch")
    with pytest.raises(UnknownTarget, match="aimchess has no motif 'nonesuch'"):
        await extractor.discover(SiteFetcher())


async def test_a_motif_is_a_file_with_its_definition_first() -> None:
    extractor = MotifsExtractor("https://aimchess.com/motif/fork")
    fetcher = SiteFetcher()
    _, [ref] = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, ref)
    assert item.pgn is not None
    game = chess.pgn.read_game(io.StringIO(item.pgn))
    assert game is not None and not game.errors
    assert (game.headers["Event"], game.headers["AimchessMotifId"]) == ("Fork", "fork")
    assert game.comment == "Two attacks at once."
    assert list(game.mainline())[1].comment == "[%cal Gf6d5,Gf6h7] A fork."
    assert (game.headers["White"], game.headers["Black"]) == ("?", "?")
