"""noctie themes: URL parsing, ordered discovery, and PGN re-export.

Every read is served from the captured fixtures by a fake fetcher, and
``auth.id_token`` is replaced, so nothing here touches Google, noctie, or the
on-disk credential file.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import chess.pgn
import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import GameItem
from pgn_dl.core.registry import resolve_key, resolve_url
from pgn_dl.sources.noctie import auth
from pgn_dl.sources.noctie.firestore import BASE
from pgn_dl.sources.noctie.themes import ThemesExtractor, theme_id_from

from .conftest import FakeFetcher, failing_fetcher

pytestmark = pytest.mark.usefixtures("no_credentials")

FIX = Path(__file__).parents[2] / "fixtures" / "noctie"

THEME_ID = "32380699-ec8a-48b4-9b3f-3e0074d74c38"
THEME_URL = f"https://app.noctie.ai/themes/{THEME_ID}"
THEME_NAME = "Beginner’s Guide to 1. e4"

# The fixture's four elements, deliberately stored out of index order.
PUZZLE_2 = "02409df4-20bc-4395-a8b9-ee613ae7dcfd"  # index 27
PUZZLE_3 = "07395ec8-2c7a-47bb-b8d3-d57d794b9951"  # index 13
SPARRING = "14d94d27-1585-476a-ab15-ccb9e68d9a14"  # index 9
PIRC = "167907e5-7abc-4d48-9b6e-c192b6a81550"  # index 40


def _elements() -> list[dict]:
    return json.loads((FIX / "theme_elements.json").read_text(encoding="utf-8"))[
        "documents"
    ]


def _element_doc(element_id: str) -> str:
    """One element as Firestore would return it from a get, not a list."""
    for doc in _elements():
        if doc["name"].rsplit("/", 1)[-1] == element_id:
            return json.dumps(doc)
    raise KeyError(element_id)


def _theme_doc(count: int | None) -> str:
    """The theme document with ``numberOfElements`` set to ``count``.

    The fixture is self-consistent, counting the four elements the listing
    fixture holds, so this is only for the cases that need it otherwise: a count
    that disagrees with the listing, and (with ``None``) a document carrying no
    count at all.
    """
    doc = json.loads((FIX / "theme_doc.json").read_text(encoding="utf-8"))
    if count is None:
        del doc["fields"]["numberOfElements"]
    else:
        doc["fields"]["numberOfElements"] = {"integerValue": str(count)}
    return json.dumps(doc)


def _fetcher() -> FakeFetcher:
    pages = {
        f"{BASE}/themes/{THEME_ID}": (FIX / "theme_doc.json").read_text(
            encoding="utf-8"
        ),
        f"{BASE}/themes/{THEME_ID}/elements": (FIX / "theme_elements.json").read_text(
            encoding="utf-8"
        ),
    }
    for doc in _elements():
        element_id = doc["name"].rsplit("/", 1)[-1]
        pages[f"{BASE}/themes/{THEME_ID}/elements/{element_id}"] = json.dumps(doc)
    return FakeFetcher(pages)


class Expiring(FakeFetcher):
    """Answers 401 to its first ``failures`` reads, then serves the fixtures.

    What an expired ID token looks like: it is minted once and lasts an hour,
    so a long download outlives it and every remaining read 401s at once.
    """

    def __init__(self, pages: dict[str, str], *, failures: int) -> None:
        super().__init__(pages)
        self.failures = failures
        self.tokens: list[str] = []

    async def get_text(self, url, *, headers):
        self.gets.append(url)
        self.tokens.append(headers.get("authorization", ""))
        if self.failures > 0:
            self.failures -= 1
            raise FetchError(f"{url} failed: 401 Missing or invalid token", status=401)
        return self.pages[url.split("?", 1)[0]]


class Stale(FakeFetcher):
    """401s any read still carrying ``token``, as Firestore does at the hour.

    Unlike :class:`Expiring` this refuses by token rather than by count, so
    every read in flight at that moment is refused, which is what puts several
    re-mints in the air at once.
    """

    def __init__(self, pages: dict[str, str], *, token: str) -> None:
        super().__init__(pages)
        self.stale = token
        self.tokens: list[str] = []

    async def get_text(self, url, *, headers):
        self.gets.append(url)
        self.tokens.append(headers.get("authorization", ""))
        await asyncio.sleep(0)  # a real read yields, so the others get in
        if self.tokens[-1] == f"Bearer {self.stale}":
            raise FetchError(f"{url} failed: 401 Missing or invalid token", status=401)
        return self.pages[url.split("?", 1)[0]]


def _counting_tokens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the minter with a counter; the returned list is what it minted."""
    minted: list[str] = []

    async def fake_id_token() -> str:
        # A real exchange is an HTTPS round trip: yield, so a second caller
        # arriving mid-mint really does get a turn.
        await asyncio.sleep(0)
        minted.append(f"tok{len(minted) + 1}")
        return minted[-1]

    monkeypatch.setattr(auth, "id_token", fake_id_token)
    return minted


async def test_an_expired_token_is_re_minted_and_the_read_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without this the hour mark fails every remaining item at once: nothing is
    # written for them, so a re-run resumes, but the tail of the run is wasted.
    minted = _counting_tokens(monkeypatch)
    fetcher = Expiring(_fetcher().pages, failures=1)

    _, refs = await ThemesExtractor(THEME_URL).discover(fetcher)

    assert len(refs) == 4  # the read went through on the retry
    theme_url = f"{BASE}/themes/{THEME_ID}"
    assert fetcher.gets[:2] == [theme_url, theme_url]  # retried once, not twice
    # A fresh token, and the retry actually carried it.
    assert minted == ["tok1", "tok2"]
    assert fetcher.tokens[:2] == ["Bearer tok1", "Bearer tok2"]
    # And it is reused: the listing does not mint a third.
    assert fetcher.tokens[2] == "Bearer tok2"


async def test_a_second_401_is_a_real_failure_and_is_not_retried_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A token seconds old that is still refused is an authorisation failure,
    # not an expiry. Re-minting on every 401 would spin instead of reporting it.
    minted = _counting_tokens(monkeypatch)
    fetcher = Expiring(_fetcher().pages, failures=99)

    with pytest.raises(FetchError) as exc:
        await ThemesExtractor(THEME_URL).discover(fetcher)

    assert "401" in str(exc.value)
    assert "tier" not in str(exc.value)  # a 401 is not the paywall message
    assert len(fetcher.gets) == 2  # one attempt, one retry, then it propagates
    assert len(minted) == 2


async def test_concurrent_expiries_share_one_re_mint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # At the hour mark every read in flight is refused at once, and at -j 4 that
    # is four of them. Each exchange rotates the refresh token and the new one
    # is persisted, so racing exchanges can leave the stored credential holding
    # a superseded token, after which no later run starts at all. One exchange,
    # and the rest reuse what it minted.
    minted = _counting_tokens(monkeypatch)
    extractor = ThemesExtractor(THEME_URL)
    _, refs = await extractor.discover(Expiring(_fetcher().pages, failures=0))
    assert minted == ["tok1"]

    fetcher = Stale(_fetcher().pages, token="tok1")
    items = await asyncio.gather(*(extractor.fetch(fetcher, r) for r in refs))

    assert len(items) == len(refs) == 4
    assert minted == ["tok1", "tok2"]
    # Every retry carried the one token that was minted, not one of its own.
    assert fetcher.tokens[4:] == ["Bearer tok2"] * 4


async def test_a_403_is_not_treated_as_an_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only 401 means expired. A denial must not cost a pointless token refresh.
    minted = _counting_tokens(monkeypatch)

    class Denying(FakeFetcher):
        async def get_text(self, url, *, headers):
            self.gets.append(url)
            raise FetchError(f"{url} failed: 403 denied", status=403)

    with pytest.raises(FetchError):
        await ThemesExtractor(THEME_URL).discover(Denying({}))
    assert len(minted) == 1


@pytest.mark.parametrize(
    "target",
    [
        THEME_URL,
        # noctie's own routes are inconsistent: themes are plural, opening
        # books singular, and users paste either.
        f"https://app.noctie.ai/theme/{THEME_ID}",
        f"{THEME_URL}/",
        f"{THEME_URL}?ref=share",
        f"{THEME_URL}#elements",
        THEME_ID,
        THEME_ID.upper(),
    ],
)
def test_theme_id_from_accepts_every_shape_of_target(target: str) -> None:
    assert theme_id_from(target) == THEME_ID


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        # Only a UUID is lowercased; these are kept as written.
        ("2025-W1", "2025-W1"),
        ("https://app.noctie.ai/themes/2026-W41", "2026-W41"),
        ("2sD1ewBJTa039iguA2nh", "2sD1ewBJTa039iguA2nh"),
        ("https://app.noctie.ai/themes/wAeQ6TBjjjzG69iR3qOf/", "wAeQ6TBjjjzG69iR3qOf"),
    ],
)
def test_theme_id_from_keeps_the_case_of_ids_that_are_not_uuids(
    target: str, expected: str
) -> None:
    assert theme_id_from(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        f"https://example.com/themes/{THEME_ID}",
        "https://app.noctie.ai/themes",
        "https://app.noctie.ai/themes/not-a-uuid",
        # A weekly scenario's id, which is case-sensitive.
        "2025-w1",
        "https://www.chess.com/lessons/learn-to-play",
        "",
    ],
)
def test_theme_id_from_rejects_anything_else(target: str) -> None:
    # noctie's SPA answers 200 for every path, so a wrong target cannot be
    # caught by the response: the pattern is the only guard.
    with pytest.raises(ValueError):
        theme_id_from(target)


def test_from_url_declines_a_foreign_url_without_raising() -> None:
    assert (
        ThemesExtractor.from_url("https://www.chess.com/lessons/learn-to-play") is None
    )
    assert isinstance(ThemesExtractor.from_url(THEME_URL), ThemesExtractor)


async def test_discover_numbers_items_by_index_not_by_arrival() -> None:
    # The fixture stores the elements as the API returned them: 27, 13, 9, 40.
    _, refs = await ThemesExtractor(THEME_URL).discover(_fetcher())
    assert [r.path for r in refs] == [
        ("009-sparring-position",),
        ("013-puzzle-3",),
        ("027-puzzle-2",),
        ("040-9-pirc-defense",),
    ]
    assert [r.id for r in refs] == [SPARRING, PUZZLE_3, PUZZLE_2, PIRC]


def _listing_with_indices(indices: list[int | None], name: str) -> str:
    """An element listing whose entries share a name and the given indices.

    Built rather than captured: the fixture's four elements have distinct
    indices, which is exactly the case that cannot collide.
    """
    documents = []
    for i, index in enumerate(indices):
        fields: dict[str, object] = {"name": {"stringValue": name}}
        if index is not None:
            fields["index"] = {"integerValue": str(index)}
        documents.append(
            {
                "name": (
                    f"projects/chessnought/databases/(default)/documents/themes/"
                    f"{THEME_ID}/elements/{i:08d}-dead-beef"
                ),
                "fields": fields,
            }
        )
    return json.dumps({"documents": documents})


@pytest.mark.parametrize(
    "indices", [[7, 7], [None, None]], ids=["repeated-index", "no-index"]
)
async def test_elements_that_would_share_a_path_are_separated_by_their_ids(
    indices: list[int | None],
) -> None:
    # Two elements on one path is the loss the runner refuses outright, which
    # costs the theme both of them on this run and every later one. The id is
    # unique, so nothing has to be lost.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/themes/{THEME_ID}"] = _theme_doc(None)
    fetcher.pages[f"{BASE}/themes/{THEME_ID}/elements"] = _listing_with_indices(
        indices, "Puzzle #1"
    )
    _, refs = await ThemesExtractor(THEME_URL).discover(fetcher)

    paths = [r.path for r in refs]
    assert len(set(paths)) == len(paths) == 2
    # Both are suffixed, so which one keeps the plain name does not depend on
    # the order the listing happened to arrive in.
    assert all(p[0].endswith(("-00000000", "-00000001")) for p in paths)


async def test_an_element_keeps_its_plain_path_when_nothing_clashes_with_it() -> None:
    # Only a clash is disambiguated: the numbering is the resume contract, and
    # suffixing every element would re-download every theme ever downloaded.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/themes/{THEME_ID}"] = _theme_doc(None)
    fetcher.pages[f"{BASE}/themes/{THEME_ID}/elements"] = _listing_with_indices(
        [7, 7, 9], "Puzzle #1"
    )
    _, refs = await ThemesExtractor(THEME_URL).discover(fetcher)

    assert refs[-1].path == ("009-puzzle-1",)


async def test_a_listing_that_matches_the_themes_own_count_is_accepted() -> None:
    # Straight off the fixtures: the theme document counts the elements the
    # listing fixture holds, so the check has nothing to complain about.
    _, refs = await ThemesExtractor(THEME_URL).discover(_fetcher())
    assert len(refs) == len(_elements()) == 4


async def test_a_short_element_listing_fails_instead_of_truncating_the_theme() -> None:
    # The theme document says how many elements it has. A listing that returns
    # fewer is silent truncation: the download reads as complete, the missing
    # elements have no files, and resume skips the theme forever after.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/themes/{THEME_ID}"] = _theme_doc(50)
    with pytest.raises(FetchError) as exc:
        await ThemesExtractor(THEME_URL).discover(fetcher)
    message = str(exc.value)
    assert "50" in message  # what the theme claims
    assert "4" in message  # what came back


async def test_a_listing_longer_than_the_count_is_read_as_a_stale_count() -> None:
    # numberOfElements is a denormalized counter that upstream may not have
    # updated. Only the other direction loses data, and failing here would leave
    # the theme with no way to be downloaded at all.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/themes/{THEME_ID}"] = _theme_doc(3)
    _, refs = await ThemesExtractor(THEME_URL).discover(fetcher)
    assert len(refs) == 4


async def test_a_theme_with_no_element_count_is_downloaded_anyway() -> None:
    # Not every theme document carries the field.
    fetcher = _fetcher()
    fetcher.pages[f"{BASE}/themes/{THEME_ID}"] = _theme_doc(None)
    _, refs = await ThemesExtractor(THEME_URL).discover(fetcher)
    assert len(refs) == 4


async def test_discover_masks_the_element_listing() -> None:
    # Without a mask every element's whole PGN is transferred at discovery and
    # then again at fetch.
    fetcher = _fetcher()
    await ThemesExtractor(THEME_URL).discover(fetcher)
    listing = next(u for u in fetcher.gets if "/elements?" in u)
    assert "mask.fieldPaths=index" in listing
    assert "mask.fieldPaths=name" in listing
    assert "mask.fieldPaths=type" in listing
    assert "mask.fieldPaths=pgn" not in listing


async def test_discover_names_the_collection_and_disambiguates_its_slug() -> None:
    collection, _ = await ThemesExtractor(THEME_URL).discover(_fetcher())
    assert collection.name == THEME_NAME
    assert collection.source == "noctie"
    assert collection.kind == "themes"
    # Theme names are not unique upstream, so the slug carries the id.
    assert collection.slug == f"beginner-s-guide-to-1-e4-{THEME_ID[:8]}"
    # No Collection.headers: this source fills GameItem.pgn, which the runner
    # writes verbatim, so collection headers would never reach a file. The
    # headers that matter are written into the PGN by fetch() instead.
    assert collection.headers == {}


async def test_fetch_rewrites_provenance_but_keeps_lichess_attribution() -> None:
    fetcher = _fetcher()
    extractor = ThemesExtractor(THEME_URL)
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[1])  # Puzzle #3

    assert isinstance(item, GameItem)
    assert item.pgn is not None
    game = chess.pgn.read_game(io.StringIO(item.pgn))
    assert game is not None
    assert game.headers["Event"] == THEME_NAME
    assert game.headers["Site"] == "noctie.ai"
    # Much of noctie's library is imported from lichess studies; that
    # attribution has to survive the rewrite.
    assert game.headers["Annotator"] == "https://lichess.org/@/andyfang"
    assert game.headers["ChapterURL"] == "https://lichess.org/study/TOb6nEN0/HZtdmTzi"
    # The element itself is identified in the file.
    assert game.headers["ElementName"] == "Puzzle #3"
    assert game.headers["NoctieElementId"] == PUZZLE_3
    # instruction / flashcard / scenario are genuinely different artifacts, so
    # a downloaded theme stays filterable by kind.
    assert game.headers["ElementType"] == "flashcard"


async def test_fetch_reads_only_the_requested_element() -> None:
    fetcher = _fetcher()
    extractor = ThemesExtractor(THEME_URL)
    _, refs = await extractor.discover(fetcher)
    before = len(fetcher.gets)
    await extractor.fetch(fetcher, refs[0])
    assert fetcher.gets[before:] == [f"{BASE}/themes/{THEME_ID}/elements/{SPARRING}"]


async def _fetch_with_pgn(pgn: str | None) -> GameItem:
    """Fetch the first element after doctoring its ``pgn`` field."""
    fetcher = _fetcher()
    url = f"{BASE}/themes/{THEME_ID}/elements/{SPARRING}"
    doc = json.loads(fetcher.pages[url])
    if pgn is None:
        del doc["fields"]["pgn"]
    else:
        doc["fields"]["pgn"] = {"stringValue": pgn}
    fetcher.pages[url] = json.dumps(doc)
    extractor = ThemesExtractor(THEME_URL)
    _, refs = await extractor.discover(fetcher)
    return await extractor.fetch(fetcher, refs[0])


async def test_an_element_with_no_pgn_fails_the_item() -> None:
    # Not a silent empty file: the runner would count it as written and then
    # skip it forever on resume. FetchError has it tallied and retried.
    with pytest.raises(FetchError):
        await _fetch_with_pgn(None)


async def test_an_unreadable_pgn_fails_the_item() -> None:
    with pytest.raises(FetchError):
        await _fetch_with_pgn("   \n  \n")


async def test_an_illegal_move_fails_the_item_instead_of_truncating_it() -> None:
    # python-chess does not stop at a move it cannot play: it records the error
    # and drops that move and everything after it, so this PGN would otherwise
    # be written as "1. e4 e5 *", a file that looks complete. The runner would
    # count it written and every later run would skip it.
    with pytest.raises(FetchError) as exc:
        await _fetch_with_pgn("1. e4 e5 2. Nf9 Nc6 3. Bb5 *")
    assert SPARRING in str(exc.value)


async def test_a_denied_element_read_explains_the_tier_too() -> None:
    # The theme document can be readable while an element is not, and an
    # unexplained 403 mid-download looks like a broken tool either way. The
    # discovery-time guards live in test_access.py, shared with opening books.
    fetcher = _fetcher()
    extractor = ThemesExtractor(THEME_URL)
    _, refs = await extractor.discover(fetcher)

    with pytest.raises(FetchError) as exc:
        await extractor.fetch(
            failing_fetcher(403, "Missing or insufficient permissions."), refs[0]
        )
    assert "tier" in str(exc.value)


def test_the_extractor_is_registered_by_key_and_by_url() -> None:
    assert resolve_key("noctie:themes") is ThemesExtractor
    assert isinstance(resolve_url(THEME_URL), ThemesExtractor)
