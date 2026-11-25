"""A user's studies: targets, the API's list, and one file per study written
as the study kind writes its chapters."""

from __future__ import annotations

import json

import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.lichess.study import StudyExtractor
from pgn_dl.sources.lichess.user_studies import UserStudiesExtractor, parse_user

from .conftest import (
    DEFENCE,
    DEFENCE_ID,
    MATE,
    MATE_ID,
    ROOT,
    SACRIFICE,
    SACRIFICE_ID,
    STUDY,
    TOKEN,
    FakeFetcher,
    Response,
    chapter_json_url,
    failure,
    save_token,
    study_export,
    study_json,
    study_json_url,
    study_url,
)

USER = "Morph_y-1"
LIST_URL = f"{ROOT}/api/study/by/{USER}"
OTHER = "Zz9yXw8V"


def listing(*studies: tuple[str, str]) -> str:
    """The API's list of a user's studies: a JSON object per line."""
    return "".join(
        json.dumps(
            {"id": i, "name": n, "createdAt": 1, "updatedAt": 2}, ensure_ascii=False
        )
        + "\n"
        for i, n in studies
    )


def lichess(**more: Response) -> FakeFetcher:
    """A user with two studies: one exports whole, the other's owner turned
    export off."""
    return FakeFetcher(
        {
            LIST_URL: listing((STUDY, "Sacrifices on h7"), (OTHER, "Quiet moves")),
            study_url(STUDY): study_export(SACRIFICE, DEFENCE),
            chapter_json_url(SACRIFICE_ID): study_json(
                SACRIFICE_ID,
                description="Take on h7.",
                study_description="Attacks on the king.",
                shows_description=True,
            ),
            chapter_json_url(DEFENCE_ID): study_json(DEFENCE_ID),
            study_url(OTHER): failure(study_url(OTHER), 403),
            study_json_url(OTHER): study_json(
                MATE_ID, study=OTHER, chapters=[(MATE_ID, "Mate")]
            ),
            f"{ROOT}/api/study/{OTHER}/{MATE_ID}.pgn": study_export(MATE),
            chapter_json_url(MATE_ID, OTHER): study_json(MATE_ID, study=OTHER),
        }
        | more
    )


@pytest.mark.parametrize(
    ("target", "user"),
    [
        (f"{ROOT}/study/by/{USER}", USER),
        (f"{ROOT}/study/by/{USER}/", USER),
        (f"{ROOT}/study/by/{USER}/hot", USER),
        (f"https://www.lichess.org/study/by/{USER}/newest?page=2#x", USER),
    ],
)
def test_targets(target: str, user: str) -> None:
    assert parse_user(target) == user


@pytest.mark.parametrize(
    "target",
    [
        USER,
        f"{ROOT}/study/by/",
        f"{ROOT}/study/by/a b",
        f"{ROOT}/study/{STUDY}",
        f"{ROOT}/@/{USER}",
        f"https://example.com/study/by/{USER}",
    ],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(ValueError):
        parse_user(target)


def test_a_user_s_studies_url_is_this_kind_s_and_a_study_s_url_is_not() -> None:
    found = registry.resolve_url(f"{ROOT}/study/by/{USER}")
    assert isinstance(found, UserStudiesExtractor)
    assert found.user == USER
    assert isinstance(registry.resolve_url(f"{ROOT}/study/{STUDY}"), StudyExtractor)


async def test_discovery_lists_one_unnumbered_file_per_study() -> None:
    fetcher = lichess()
    collection, refs = await UserStudiesExtractor(f"{ROOT}/study/by/{USER}").discover(
        fetcher
    )
    assert (collection.source, collection.kind) == ("lichess", "user-studies")
    assert (collection.slug, collection.name) == ("morph-y-1", f"Studies by {USER}")
    # The id keeps two studies of one name apart.
    assert [(r.id, r.path) for r in refs] == [
        (STUDY, ("sacrifices-on-h7-h7sac1fz",)),
        (OTHER, ("quiet-moves-zz9yxw8v",)),
    ]
    assert fetcher.urls == [LIST_URL]


async def test_the_saved_token_goes_with_the_list() -> None:
    save_token()
    fetcher = lichess()
    await UserStudiesExtractor(f"{ROOT}/study/by/{USER}").discover(fetcher)
    [(_, _, headers, _)] = fetcher.requests
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_a_study_is_one_file_of_its_finished_chapters() -> None:
    fetcher = lichess()
    extractor = UserStudiesExtractor(f"{ROOT}/study/by/{USER}")
    _, refs = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, refs[0])
    first = SACRIFICE.replace(
        "{ [%cal Gd3h7] }",
        "{ Attacks on the king. } { Take on h7. } { [%cal Gd3h7] }",
    )
    # Joined as the export joins them.
    assert item.pgn == f"{first}\n\n\n{DEFENCE}"


async def test_a_study_with_export_off_is_read_chapter_by_chapter() -> None:
    fetcher = lichess()
    extractor = UserStudiesExtractor(f"{ROOT}/study/by/{USER}")
    _, refs = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, refs[1])).pgn == MATE


async def test_a_name_with_a_line_separator_in_it_is_one_study() -> None:
    fetcher = lichess(**{LIST_URL: listing((STUDY, "Line\u2028separator"))})
    _, refs = await UserStudiesExtractor(f"{ROOT}/study/by/{USER}").discover(fetcher)
    assert [r.id for r in refs] == [STUDY]


async def test_a_user_with_no_studies_listed_is_refused() -> None:
    fetcher = lichess(**{LIST_URL: ""})
    with pytest.raises(FetchError, match=f"lichess lists no studies by {USER}"):
        await UserStudiesExtractor(f"{ROOT}/study/by/{USER}").discover(fetcher)


async def test_a_refused_token_says_to_log_in_again() -> None:
    save_token()
    fetcher = lichess(**{LIST_URL: failure(LIST_URL, 401)})
    with pytest.raises(FetchError, match="refused the saved token"):
        await UserStudiesExtractor(f"{ROOT}/study/by/{USER}").discover(fetcher)


@pytest.mark.parametrize(
    "line", [json.dumps({"name": "No id"}), json.dumps({"id": "short"}), "[1]"]
)
async def test_a_listed_study_without_an_id_is_refused(line: str) -> None:
    fetcher = lichess(**{LIST_URL: line + "\n"})
    with pytest.raises(FetchError, match=f"a study by {USER} without an id"):
        await UserStudiesExtractor(f"{ROOT}/study/by/{USER}").discover(fetcher)
