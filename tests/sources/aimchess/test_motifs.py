"""Tactical motifs: target, the motif table, one file per motif."""

from __future__ import annotations

import io

import chess.pgn
import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.aimchess.motifs import MotifsExtractor, parse_motifs

from .conftest import APP_SCRIPT, SiteFetcher


def test_any_motif_url_resolves_to_this_extractor() -> None:
    assert isinstance(
        registry.resolve_url("https://aimchess.com/motif/pin"), MotifsExtractor
    )
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


async def test_each_motif_is_a_file_with_its_definition_first() -> None:
    extractor = MotifsExtractor("https://aimchess.com/motif/pin")
    fetcher = SiteFetcher()
    collection, refs = await extractor.discover(fetcher)
    assert (collection.kind, collection.slug) == ("motifs", "tactical-motifs")
    assert [r.path for r in refs] == [("01-fork",), ("02-doble",)]
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn is not None
    game = chess.pgn.read_game(io.StringIO(item.pgn))
    assert game is not None and not game.errors
    assert (game.headers["Event"], game.headers["AimchessMotifId"]) == ("Fork", "fork")
    assert game.comment == "Two attacks at once."
    assert list(game.mainline())[1].comment == "[%cal Gf6d5,Gf6h7] A fork."
    assert (game.headers["White"], game.headers["Black"]) == ("?", "?")
