"""An extractor may hand the runner finished PGN instead of a move tree."""

from __future__ import annotations

from pathlib import Path

from pgn_dl.core.extractor import Extractor
from pgn_dl.core.models import Collection, GameItem, ItemRef, MoveNode
from pgn_dl.core.runner import Options, run

COLLECTION = Collection(
    source="fake", kind="things", slug="c", name="C", headers={"Event": "C"}
)
PRERENDERED = '[Event "Given"]\n[Result "*"]\n\n1. e4 e5 *'


class FetcherStub:
    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("no network in tests")

    async def get_text(self, url, *, headers):  # pragma: no cover
        raise AssertionError("no network in tests")


class PgnExtractor(Extractor[None]):
    key = "fake:pgntest"
    needs_browser = False

    def __init__(self, *, prerendered: bool) -> None:
        super().__init__()
        self.prerendered = prerendered

    async def _discover(self, fetcher):
        return COLLECTION, [ItemRef(id="1", path=("001-item",))], None

    async def _fetch(self, context, fetcher, ref):
        if self.prerendered:
            return GameItem(pgn=PRERENDERED)
        return GameItem(root=MoveNode(san=None, children=[MoveNode(san="d4")]))


async def test_prerendered_pgn_is_written_verbatim(tmp_path: Path) -> None:
    await run(
        PgnExtractor(prerendered=True),
        FetcherStub(),
        Options(out=tmp_path, concurrency=1, delay=0.0),
        echo=lambda _: None,
    )
    text = (tmp_path / "fake/things/c/001-item.pgn").read_text(encoding="utf-8")
    assert text.startswith(PRERENDERED)
    # The collection's own Event header must NOT be grafted on: a pre-rendered
    # item owns its headers outright.
    assert '[Event "Given"]' in text
    assert '[Event "C"]' not in text


async def test_move_tree_path_is_unchanged(tmp_path: Path) -> None:
    await run(
        PgnExtractor(prerendered=False),
        FetcherStub(),
        Options(out=tmp_path, concurrency=1, delay=0.0),
        echo=lambda _: None,
    )
    text = (tmp_path / "fake/things/c/001-item.pgn").read_text(encoding="utf-8")
    assert '[Event "C"]' in text  # collection headers still applied
    assert "d4" in text
