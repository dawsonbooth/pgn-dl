"""Practice: its fixed structure, targets, one file per study in a directory
per section, each written as Lichess exports it with the study's line and each
chapter's description added, and asked for with no token."""

from __future__ import annotations

import re

import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess.practice import (
    SECTIONS,
    PracticeExtractor,
    Scope,
    layout,
    scope_from,
)
from pgn_dl.sources.lichess.study import ID

from .conftest import (
    ROOT,
    SACRIFICE_FEN,
    USER_AGENT,
    FakeFetcher,
    chapter,
    chapter_json_url,
    save_token,
    study_export,
    study_json,
    study_url,
)

PRACTICE = f"{ROOT}/practice"
GREEK_GIFT = "s5pLU7Of"


def greek_gift_chapter(chapter_id: str, name: str, moves: str, goal: str) -> str:
    return chapter(
        chapter_id,
        name,
        moves,
        fen=SACRIFICE_FEN,
        study=GREEK_GIFT,
        study_name="Lichess Practice: Greek Gift",
        Event=f"Lichess Practice: Greek Gift: {name}",
        Termination=goal,
    )


INTRODUCTION, CHALLENGE = "uV9wX1yZ", "aA1bB2cC"
#: The introduction carries a sideline python-chess cannot play, as Lichess's
#: own does: Black's rook stands on f8.
GREEK_GIFT_EXPORT = study_export(
    greek_gift_chapter(
        INTRODUCTION,
        "Greek Gift Introduction",
        "{ [%cal Gd3h7] } 1. Bxh7+ Kxh7 { The king is exposed. } (1... Kf8) *",
        "+300cp in 1",
    ),
    greek_gift_chapter(CHALLENGE, "Greek Gift Challenge #1", " *", "mate in 7"),
)


def greek_gift(**more: str | Exception) -> FakeFetcher:
    """Lichess for the Greek Gift study: its export, and its chapters' JSON,
    each with the description shown beside the board."""
    return FakeFetcher(
        {
            study_url(GREEK_GIFT): GREEK_GIFT_EXPORT,
            chapter_json_url(INTRODUCTION, GREEK_GIFT): study_json(
                INTRODUCTION, study=GREEK_GIFT, description="Take on h7 with check."
            ),
            chapter_json_url(CHALLENGE, GREEK_GIFT): study_json(
                CHALLENGE, study=GREEK_GIFT, description="Mate in 7 if played well."
            ),
        }
        | more
    )


def test_practice_is_five_sections_of_thirty_two_studies() -> None:
    assert [s.id for s in SECTIONS] == [
        "checkmates",
        "fundamental-tactics",
        "advanced-tactics",
        "pawn-endgames",
        "rook-endgames",
    ]
    studies = [study for section in SECTIONS for study in section.studies]
    assert len(studies) == 32
    assert len({study.id for study in studies}) == 32
    assert all(re.fullmatch(ID, study.id) for study in studies)
    assert all(study.name and study.description for study in studies)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (PRACTICE, Scope()),
        (f"{PRACTICE}/", Scope()),
        ("https://www.lichess.org/practice?x=1#y", Scope()),
        (f"{PRACTICE}/rook-endgames", Scope(section="rook-endgames")),
        (f"{PRACTICE}/rook-endgames/", Scope(section="rook-endgames")),
        (
            f"{PRACTICE}/advanced-tactics/greek-gift/{GREEK_GIFT}",
            Scope(section="advanced-tactics", study=GREEK_GIFT),
        ),
        (
            f"{PRACTICE}/advanced-tactics/greek-gift/{GREEK_GIFT}/uV9wX1yZ",
            Scope(section="advanced-tactics", study=GREEK_GIFT),
        ),
        (GREEK_GIFT, Scope(study=GREEK_GIFT)),
        (f"  {GREEK_GIFT}\n", Scope(study=GREEK_GIFT)),
    ],
)
def test_targets(target: str, expected: Scope) -> None:
    assert scope_from(target) == expected


@pytest.mark.parametrize(
    ("target", "message"),
    [
        (f"{PRACTICE}/openings", "has no section 'openings'"),
        ("h7Sac1fz", "'h7Sac1fz' is not a lichess practice study"),
        (f"{PRACTICE}/checkmates/a-study/h7Sac1fz", "is not a lichess practice study"),
        (f"{ROOT}/study/{GREEK_GIFT}", "could not read lichess practice"),
        (f"{PRACTICE}/advanced-tactics/greek-gift", "could not read lichess practice"),
        ("https://example.com/practice", "could not read lichess practice"),
        ("", "could not read lichess practice"),
    ],
)
def test_other_targets_are_refused(target: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        scope_from(target)


def test_practice_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url(f"{PRACTICE}/pawn-endgames")
    assert isinstance(found, PracticeExtractor)
    assert found.scope == Scope(section="pawn-endgames")
    assert PracticeExtractor.from_url(f"{ROOT}/practices") is None


def test_each_study_has_a_path_of_its_own_in_a_numbered_directory_per_section() -> None:
    placed = layout()
    assert [study.id for study, _ in placed] == [
        study.id for section in SECTIONS for study in section.studies
    ]
    assert len({path for _, path in placed}) == 32
    paths = {study.id: path for study, path in placed}
    assert paths["BJy6fEDf"] == ("01-checkmates", "01-piece-checkmates-i")
    assert paths["9ogFv8Ac"] == ("02-fundamental-tactics", "01-the-pin")
    assert paths[GREEK_GIFT] == ("03-advanced-tactics", "03-greek-gift")
    assert paths["wS23j5Tm"] == ("05-rook-endgames", "04-practical-rook-endings")
    # Two studies are both "7th-Rank Rook Pawn"; their sections keep them apart.
    assert paths["pt20yRkT"] == ("04-pawn-endgames", "03-7th-rank-rook-pawn")
    assert paths["MkDViieT"] == ("05-rook-endgames", "01-7th-rank-rook-pawn")


async def test_discovery_lists_every_study_without_asking_lichess() -> None:
    fetcher = FakeFetcher({})
    collection, refs = await PracticeExtractor(PRACTICE).discover(fetcher)
    assert (collection.source, collection.kind) == ("lichess", "practice")
    assert (collection.slug, collection.name) == ("practice", "Practice")
    assert [(r.id, r.path) for r in refs] == [(s.id, path) for s, path in layout()]
    assert fetcher.requests == []


@pytest.mark.parametrize(
    ("target", "ids"),
    [
        (f"{PRACTICE}/pawn-endgames", ["xebrDvFe", "A4ujYOer", "pt20yRkT"]),
        (f"{PRACTICE}/advanced-tactics/greek-gift/{GREEK_GIFT}", [GREEK_GIFT]),
        (GREEK_GIFT, [GREEK_GIFT]),
    ],
)
async def test_a_section_or_a_study_keeps_the_paths_of_a_full_download(
    target: str, ids: list[str]
) -> None:
    _, everything = await PracticeExtractor(PRACTICE).discover(FakeFetcher({}))
    _, some = await PracticeExtractor(target).discover(FakeFetcher({}))
    assert [r.id for r in some] == ids
    assert some == [r for r in everything if r.id in ids]


async def test_a_study_under_another_section_s_url_is_refused() -> None:
    target = f"{PRACTICE}/checkmates/greek-gift/{GREEK_GIFT}"
    with pytest.raises(FetchError, match=f"{GREEK_GIFT} is not in section checkmates"):
        await PracticeExtractor(target).discover(FakeFetcher({}))


async def test_a_study_is_one_file_of_its_chapters_as_lichess_exports_them() -> None:
    fetcher = greek_gift()
    extractor = PracticeExtractor(GREEK_GIFT)
    _, [ref] = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, ref)
    # Every chapter and its goal, the unplayable sideline included; the
    # study's line from the practice page first, then each chapter's own
    # description.
    assert item.pgn == (
        GREEK_GIFT_EXPORT.strip()
        .replace(
            "{ [%cal Gd3h7] }",
            "{ Study the greek gift sacrifice } { Take on h7 with check. } "
            "{ [%cal Gd3h7] }",
        )
        .replace(
            '"mate in 7"]\n\n *', '"mate in 7"]\n\n { Mate in 7 if played well. } *'
        )
    )
    assert "(1... Kf8)" in item.pgn
    assert '[Termination "mate in 7"]' in item.pgn
    assert [(m, u, b) for m, u, _, b in fetcher.requests] == [
        ("GET", f"{ROOT}/api/study/{GREEK_GIFT}.pgn", None),
        ("GET", f"{ROOT}/study/{GREEK_GIFT}/{INTRODUCTION}", None),
        ("GET", f"{ROOT}/study/{GREEK_GIFT}/{CHALLENGE}", None),
    ]
    assert fetcher.requests[0][2] == {"User-Agent": USER_AGENT}


async def test_a_saved_token_is_never_sent_for_practice() -> None:
    # Practice is public, so a stale token cannot get in the way.
    save_token()
    fetcher = greek_gift()
    extractor = PracticeExtractor(GREEK_GIFT)
    _, refs = await extractor.discover(fetcher)
    await extractor.fetch(fetcher, refs[0])
    assert len(fetcher.requests) == 3
    assert not any("Authorization" in h for _, _, h, _ in fetcher.requests)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", f"lichess study {GREEK_GIFT} has no chapters"),
        (
            "<!DOCTYPE html><html><body>Not found</body></html>",
            f"lichess study {GREEK_GIFT} is not a study export",
        ),
    ],
)
async def test_an_export_without_study_chapters_fails_the_study(
    text: str, message: str
) -> None:
    fetcher = greek_gift(**{study_url(GREEK_GIFT): text})
    extractor = PracticeExtractor(GREEK_GIFT)
    _, refs = await extractor.discover(fetcher)
    with pytest.raises(FetchError, match=message):
        await extractor.fetch(fetcher, refs[0])
