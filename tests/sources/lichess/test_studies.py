"""Studies: targets, the export sliced into chapters as Lichess wrote them,
Lichess's refusals in words, one file per chapter, and what the export leaves
out added from each chapter's JSON."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

import chess
import pytest

from pgn_dl.core import registry
from pgn_dl.core.fetch import Blocked, FetchError
from pgn_dl.sources.lichess.client import Client
from pgn_dl.sources.lichess.studies import (
    StudiesExtractor,
    export,
    parse_target,
    read_chapters,
    study_json,
)

from .conftest import (
    CHALLENGE_PAGE,
    DEFENCE,
    DEFENCE_ID,
    MATE,
    MATE_ID,
    ROOT,
    SACRIFICE,
    SACRIFICE_FEN,
    SACRIFICE_ID,
    STUDY,
    STUDY_NAME,
    TOKEN,
    FakeFetcher,
    Response,
    chapter,
    chapter_json_url,
    failure,
    save_token,
    study_export,
    study_json_url,
    study_url,
)
from .conftest import study_json as json_of

SUBJECT = f"lichess study {STUDY}"
EXPORT = study_export(SACRIFICE, DEFENCE, MATE)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("https://lichess.org/study/h7Sac1fz", ("h7Sac1fz", None)),
        ("https://lichess.org/study/h7Sac1fz/", ("h7Sac1fz", None)),
        ("http://www.lichess.org/study/h7Sac1fz", ("h7Sac1fz", None)),
        ("https://lichess.org/study/h7Sac1fz/Pq7XcV2m", ("h7Sac1fz", "Pq7XcV2m")),
        ("https://lichess.org/study/h7Sac1fz.pgn", ("h7Sac1fz", None)),
        ("https://lichess.org/study/h7Sac1fz/Pq7XcV2m.pgn", ("h7Sac1fz", "Pq7XcV2m")),
        ("https://lichess.org/study/h7Sac1fz#chapters", ("h7Sac1fz", None)),
        ("https://lichess.org/study/h7Sac1fz/Pq7XcV2m?x=1#y", ("h7Sac1fz", "Pq7XcV2m")),
        ("h7Sac1fz", ("h7Sac1fz", None)),
        ("  h7Sac1fz\n", ("h7Sac1fz", None)),
    ],
)
def test_targets(target: str, expected: tuple[str, str | None]) -> None:
    assert parse_target(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        "",
        "h7Sac1f",  # an id is eight characters
        "h7Sac1fz9",
        "h7Sac-fz",
        "https://lichess.org/study",
        "https://lichess.org/study/h7Sac1fz/Pq7X",
        "https://lichess.org/api/study/h7Sac1fz.pgn",
        "https://example.com/study/h7Sac1fz",
        "https://lichess.org/practice/checkmates/piece-checkmates-i/BJy6fEDf",
    ],
)
def test_other_targets_are_refused(target: str) -> None:
    with pytest.raises(ValueError):
        parse_target(target)


def test_study_and_chapter_urls_resolve_to_this_extractor() -> None:
    found = registry.resolve_url(f"{ROOT}/study/{STUDY}/{DEFENCE_ID}")
    assert isinstance(found, StudiesExtractor)
    assert (found.study, found.chapter) == (STUDY, DEFENCE_ID)
    assert StudiesExtractor.from_url(f"{ROOT}/study/{STUDY}9") is None


# Reading an export.


def test_each_chapter_is_sliced_out_of_the_export_as_lichess_wrote_it() -> None:
    chapters = read_chapters(EXPORT, subject=SUBJECT)
    assert [c.text for c in chapters] == [SACRIFICE, DEFENCE, MATE]
    assert [(c.id, c.name, c.study_name) for c in chapters] == [
        (SACRIFICE_ID, "The bishop takes on h7", STUDY_NAME),
        (DEFENCE_ID, "The knight on f6 defends", STUDY_NAME),
        (MATE_ID, "Queen and knight mate", STUDY_NAME),
    ]


def test_a_move_python_chess_cannot_play_is_kept_and_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The sideline really is unplayable: Black's own rook stands on f8.
    board = chess.Board(SACRIFICE_FEN)
    board.push_san("Bxh7+")
    with pytest.raises(chess.IllegalMoveError):
        board.parse_san("Kf8")

    with caplog.at_level(logging.DEBUG):
        first, after = read_chapters(study_export(SACRIFICE, DEFENCE), subject=SUBJECT)
    assert first.text == SACRIFICE
    assert "(1... Kf8) *" in first.text
    # Reading goes on past it: the next chapter starts where it should.
    assert (after.id, after.text) == (DEFENCE_ID, DEFENCE)
    assert not [r for r in caplog.records if r.name.startswith("chess")]


def test_a_chapter_without_its_url_or_name_is_known_by_number_and_event() -> None:
    nameless = chapter(MATE_ID, "Mate", "1. Qh7# *", ChapterURL=None, ChapterName=None)
    [only] = read_chapters(study_export(nameless), subject=SUBJECT)
    assert (only.id, only.name) == ("1", f"{STUDY_NAME}: Mate")


#: A game's export: PGN, but no study's.
GAME = (
    '[Event "Rated blitz game"]\n[Site "https://lichess.org/abcdEFGH"]\n'
    '[Result "1-0"]\n\n1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0\n'
)


@pytest.mark.parametrize(
    "text",
    [
        GAME,
        "<!DOCTYPE html><html><body><h1>Page not found</h1></body></html>",
        study_export(SACRIFICE, chapter(DEFENCE_ID, "Lost", "*", StudyName=None)),
    ],
)
def test_an_export_that_is_not_all_study_chapters_is_refused(text: str) -> None:
    with pytest.raises(FetchError, match=f"^{SUBJECT} is not a study export$"):
        read_chapters(text, subject=SUBJECT)


def test_an_empty_export_has_no_chapters() -> None:
    assert read_chapters("", subject=SUBJECT) == []


def test_an_export_cut_short_is_refused() -> None:
    # Written, it would be skipped by every later run.
    cut = SACRIFICE[: SACRIFICE.index("Kxh7") + len("Kxh7")]
    with pytest.raises(FetchError, match=f"^{SUBJECT} came back cut short$"):
        read_chapters(study_export(DEFENCE, cut), subject=SUBJECT)


def test_a_broadcast_round_is_named_as_one() -> None:
    # Lichess lists a user's broadcast rounds among their studies, and exports
    # their games without a study's headers.
    game = chapter(
        MATE_ID,
        "Round 1",
        "1. Qh7# *",
        StudyName=None,
        ChapterName=None,
        ChapterURL=None,
        BroadcastName="An open",
        BroadcastURL=f"{ROOT}/broadcast/an-open/round-1/{STUDY}",
    )
    with pytest.raises(FetchError, match="is a broadcast round"):
        read_chapters(study_export(game), subject=SUBJECT)


# The export request, and Lichess's refusals.


async def test_the_export_is_asked_of_the_api_accepting_anything() -> None:
    # Accepting only PGN, a request with a token gets a refusal as 406 ("This
    # endpoint only returns JSON") rather than the 403 that says why.
    fetcher = FakeFetcher({study_url(STUDY): EXPORT})
    assert await export(Client(fetcher, token=TOKEN), STUDY) == EXPORT
    [(method, url, headers, body)] = fetcher.requests
    assert (method, url, body) == ("GET", f"{ROOT}/api/study/{STUDY}.pgn", None)
    assert "Accept" not in headers


@pytest.mark.parametrize(("send_token", "sent"), [(True, True), (False, False)])
async def test_the_saved_token_goes_with_the_export_unless_told_not_to(
    send_token: bool, sent: bool
) -> None:
    fetcher = FakeFetcher({study_url(STUDY): EXPORT})
    await export(Client(fetcher, token=TOKEN), STUDY, send_token=send_token)
    [(_, _, headers, _)] = fetcher.requests
    assert headers.get("Authorization") == (f"Bearer {TOKEN}" if sent else None)


REFUSED = (
    f"lichess study {STUDY} cannot be downloaded: it is private, or its owner "
    "lets only some accounts export it"
)


@pytest.mark.parametrize(
    ("status", "token", "message"),
    [
        (401, TOKEN, "Lichess refused the saved token; run: pgn-dl login lichess"),
        (
            401,
            None,
            (
                f"{SUBJECT} is private; if your account may see it, run: "
                "pgn-dl login lichess"
            ),
        ),
        (403, TOKEN, f"{REFUSED}; the saved token's account is not one of them"),
        (403, None, f"{REFUSED}; if yours is one, run: pgn-dl login lichess"),
        (404, None, f"lichess has no study {STUDY}"),
        (404, TOKEN, f"lichess has no study {STUDY}"),
    ],
)
async def test_lichess_refusals_are_put_in_words(
    status: int, token: str | None, message: str
) -> None:
    fetcher = FakeFetcher({study_url(STUDY): failure(study_url(STUDY), status)})
    with pytest.raises(FetchError) as exc:
        await export(Client(fetcher, token=token), STUDY)
    assert str(exc.value) == message
    assert exc.value.status == status


async def test_a_private_study_asked_for_without_the_token_does_not_blame_it() -> None:
    # The advice follows what this request carried: no token was sent, so the
    # saved one was never tried and cannot be what Lichess refused.
    fetcher = FakeFetcher({study_url(STUDY): failure(study_url(STUDY), 403)})
    with pytest.raises(FetchError) as exc:
        await export(Client(fetcher, token=TOKEN), STUDY, send_token=False)
    assert str(exc.value) == f"{REFUSED}; if yours is one, run: pgn-dl login lichess"


async def test_a_bot_challenge_reaches_the_runner_as_a_block_not_as_private(
    waits: list[float],
) -> None:
    challenge = failure(study_url(STUDY), 403, CHALLENGE_PAGE)
    fetcher = FakeFetcher({study_url(STUDY): challenge})
    with pytest.raises(Blocked) as exc:
        await export(Client(fetcher), STUDY)
    assert exc.value is challenge
    assert waits == []  # a challenge is not a rate limit to wait out


async def test_any_other_failure_is_passed_on_as_it_is() -> None:
    refused = failure(study_url(STUDY), 500)
    fetcher = FakeFetcher({study_url(STUDY): refused})
    with pytest.raises(FetchError) as exc:
        await export(Client(fetcher), STUDY)
    assert exc.value is refused


# A study's JSON, and its refusals.


LISTED = [
    (SACRIFICE_ID, "The bishop takes on h7"),
    (DEFENCE_ID, "The knight on f6 defends"),
    (MATE_ID, "Queen and knight mate"),
]


async def test_json_is_asked_for_as_json_with_the_saved_token() -> None:
    fetcher = FakeFetcher({study_json_url(): json_of(SACRIFICE_ID, chapters=LISTED)})
    data = await study_json(Client(fetcher, token=TOKEN), STUDY)
    assert [c["id"] for c in data["study"]["chapters"]] == [i for i, _ in LISTED]
    [(method, url, headers, _)] = fetcher.requests
    assert (method, url) == ("GET", f"{ROOT}/study/{STUDY}?chapters=true")
    assert headers["Accept"] == "application/json"
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_a_chapter_s_json_is_the_chapter_s_own_url() -> None:
    fetcher = FakeFetcher({chapter_json_url(MATE_ID): json_of(MATE_ID)})
    data = await study_json(Client(fetcher), STUDY, MATE_ID, send_token=False)
    assert data["study"]["chapter"]["id"] == MATE_ID
    assert fetcher.urls == [f"{ROOT}/study/{STUDY}/{MATE_ID}"]


@pytest.mark.parametrize(
    ("status", "token", "message"),
    [
        (401, TOKEN, "Lichess refused the saved token; run: pgn-dl login lichess"),
        (
            401,
            None,
            (
                f"{SUBJECT} chapter {MATE_ID} is private; if your account may "
                "see it, run: pgn-dl login lichess"
            ),
        ),
        (
            403,
            TOKEN,
            (
                f"{SUBJECT} chapter {MATE_ID} is private, and the saved token's "
                "account may not see it"
            ),
        ),
        (404, None, f"lichess has no chapter {MATE_ID} in study {STUDY}"),
    ],
)
async def test_json_refusals_are_put_in_words(
    status: int, token: str | None, message: str
) -> None:
    url = chapter_json_url(MATE_ID)
    fetcher = FakeFetcher({url: failure(url, status)})
    with pytest.raises(FetchError) as exc:
        await study_json(Client(fetcher, token=token), STUDY, MATE_ID)
    assert str(exc.value) == message
    assert exc.value.status == status


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (json_of(MATE_ID, study="Other123"), "another study than"),
        (json_of(DEFENCE_ID), "another chapter than"),
        (json.dumps({"analysis": {}}), "another study than"),
        (json.dumps([1, 2]), "another study than"),
    ],
)
async def test_json_for_something_else_is_refused(answer: str, message: str) -> None:
    fetcher = FakeFetcher({chapter_json_url(MATE_ID): answer})
    with pytest.raises(FetchError, match=message):
        await study_json(Client(fetcher), STUDY, MATE_ID)


async def test_json_that_will_not_decode_is_left_to_the_runner() -> None:
    fetcher = FakeFetcher({chapter_json_url(MATE_ID): "<html>Not JSON</html>"})
    with pytest.raises(ValueError):
        await study_json(Client(fetcher), STUDY, MATE_ID)


# The extractor.


def exported(**more: Response) -> FakeFetcher:
    """Lichess for a study that exports whole, each chapter without a
    description: what the export leaves out is nothing."""
    return FakeFetcher(
        {
            study_url(STUDY): EXPORT,
            chapter_json_url(SACRIFICE_ID): json_of(SACRIFICE_ID),
            chapter_json_url(DEFENCE_ID): json_of(DEFENCE_ID),
            chapter_json_url(MATE_ID): json_of(MATE_ID),
        }
        | more
    )


def test_a_study_opens_no_window() -> None:
    assert StudiesExtractor.reads_pages is False
    assert StudiesExtractor.needs_browser is False


async def test_discovery_reads_the_study_once_and_numbers_its_chapters() -> None:
    fetcher = exported()
    collection, refs = await StudiesExtractor(f"{ROOT}/study/{STUDY}").discover(fetcher)
    assert (collection.source, collection.kind) == ("lichess", "studies")
    # The id keeps two studies of one name apart.
    assert (collection.slug, collection.name) == (
        f"sacrifices-on-h7-{STUDY}",
        STUDY_NAME,
    )
    assert [(r.id, r.path) for r in refs] == [
        (SACRIFICE_ID, ("01-the-bishop-takes-on-h7",)),
        (DEFENCE_ID, ("02-the-knight-on-f6-defends",)),
        (MATE_ID, ("03-queen-and-knight-mate",)),
    ]
    assert fetcher.urls == [study_url(STUDY)]


async def test_a_chapter_url_gets_that_chapter_under_its_number() -> None:
    fetcher = exported()
    extractor = StudiesExtractor(f"{ROOT}/study/{STUDY}/{DEFENCE_ID}")
    collection, refs = await extractor.discover(fetcher)
    assert collection.slug == f"sacrifices-on-h7-{STUDY}"
    assert [(r.id, r.path) for r in refs] == [
        (DEFENCE_ID, ("02-the-knight-on-f6-defends",))
    ]
    item = await extractor.fetch(fetcher, refs[0])
    assert item.pgn == DEFENCE


async def test_each_chapter_is_written_as_exported_after_asking_its_json() -> None:
    fetcher = exported()
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    items = [await extractor.fetch(fetcher, ref) for ref in refs]
    assert [item.pgn for item in items] == [SACRIFICE, DEFENCE, MATE]
    assert fetcher.urls == [
        study_url(STUDY),
        chapter_json_url(SACRIFICE_ID),
        chapter_json_url(DEFENCE_ID),
        chapter_json_url(MATE_ID),
    ]


async def test_a_chapter_the_study_does_not_have_is_refused() -> None:
    fetcher = exported()
    with pytest.raises(
        FetchError, match=f"lichess has no chapter ZZZZ9999 in study {STUDY}"
    ):
        await StudiesExtractor(f"{ROOT}/study/{STUDY}/ZZZZ9999").discover(fetcher)


async def test_a_study_with_no_chapters_is_refused() -> None:
    fetcher = FakeFetcher({study_url(STUDY): ""})
    with pytest.raises(FetchError, match=f"{SUBJECT} has no chapters"):
        await StudiesExtractor(STUDY).discover(fetcher)


async def test_a_nameless_study_or_chapter_falls_back_to_id_and_number() -> None:
    unnamed = chapter(MATE_ID, "♕", "1. Qh7# *", StudyName="", Event="♕")
    fetcher = FakeFetcher({study_url(STUDY): study_export(unnamed)})
    collection, refs = await StudiesExtractor(STUDY).discover(fetcher)
    assert (collection.slug, collection.name) == (f"h7sac1fz-{STUDY}", STUDY)
    assert [r.path for r in refs] == [("01-chapter",)]


async def test_the_saved_token_goes_with_every_request() -> None:
    save_token()
    fetcher = exported()
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    await extractor.fetch(fetcher, refs[0])
    assert [h.get("Authorization") for _, _, h, _ in fetcher.requests] == [
        f"Bearer {TOKEN}",
        f"Bearer {TOKEN}",
    ]


async def test_without_a_saved_token_nothing_is_sent_in_its_place() -> None:
    fetcher = exported()
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    await extractor.fetch(fetcher, refs[0])
    assert not any("Authorization" in h for _, _, h, _ in fetcher.requests)


async def test_a_private_study_says_to_log_in_when_no_token_is_saved() -> None:
    fetcher = FakeFetcher(
        {
            study_url(STUDY): failure(study_url(STUDY), 401),
        }
    )
    with pytest.raises(FetchError, match="is private; if your account may see it"):
        await StudiesExtractor(STUDY).discover(fetcher)


# What the export leaves out: the descriptions.


async def test_a_chapter_s_description_opens_its_file() -> None:
    fetcher = exported(
        **{
            chapter_json_url(DEFENCE_ID): json_of(
                DEFENCE_ID, description="  Defend h7 with the knight.\n"
            )
        }
    )
    extractor = StudiesExtractor(f"{ROOT}/study/{STUDY}/{DEFENCE_ID}")
    _, [ref] = await extractor.discover(fetcher)
    item = await extractor.fetch(fetcher, ref)
    assert item.pgn == DEFENCE.replace(
        "\n\n1. e4", "\n\n{ Defend h7 with the knight. } 1. e4"
    )


async def test_the_study_s_description_opens_its_first_chapter_only() -> None:
    def answer(shown: str, description: str | None = None) -> str:
        return json_of(
            shown,
            description=description,
            study_description="Attacks on the king.",
            shows_description=True,
        )

    fetcher = exported(
        **{
            chapter_json_url(SACRIFICE_ID): answer(SACRIFICE_ID, "Take on h7."),
            chapter_json_url(DEFENCE_ID): answer(DEFENCE_ID),
        }
    )
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    first, second = [await extractor.fetch(fetcher, ref) for ref in refs[:2]]
    assert first.pgn == SACRIFICE.replace(
        "{ [%cal Gd3h7] }",
        "{ Attacks on the king. } { Take on h7. } { [%cal Gd3h7] }",
    )
    assert second.pgn == DEFENCE


async def test_a_description_switched_on_but_never_written_is_left_out() -> None:
    # lila keeps "-" for one, and its page shows nothing.
    answer = json_of(
        SACRIFICE_ID,
        description="-",
        study_description="-",
        shows_description=True,
    )
    fetcher = exported(**{chapter_json_url(SACRIFICE_ID): answer})
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, refs[0])).pgn == SACRIFICE


class Rewinding(FakeFetcher):
    """Answers a chapter's JSON once with the first chapter, as lila does
    when it rewinds a study left idle with its position elsewhere."""

    async def get_text(self, url: str, *, headers: Mapping[str, str]) -> str:
        answer = await super().get_text(url, headers=headers)
        if url == chapter_json_url(DEFENCE_ID):
            self.responses[url] = json_of(DEFENCE_ID)
        return answer


async def test_a_chapter_answered_with_the_first_is_asked_for_again() -> None:
    asked = chapter_json_url(DEFENCE_ID)
    fetcher = Rewinding(exported(**{asked: json_of(SACRIFICE_ID)}).responses)
    extractor = StudiesExtractor(f"{ROOT}/study/{STUDY}/{DEFENCE_ID}")
    _, [ref] = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, ref)).pgn == DEFENCE
    assert fetcher.urls == [study_url(STUDY), asked, asked]


async def test_a_study_description_the_study_does_not_show_is_left_out() -> None:
    fetcher = exported(
        **{
            chapter_json_url(SACRIFICE_ID): json_of(
                SACRIFICE_ID, study_description="An old note."
            )
        }
    )
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, refs[0])).pgn == SACRIFICE


async def test_a_chapter_whose_json_fails_fails_alone() -> None:
    # Written without its description, it would be skipped by every later run.
    url = chapter_json_url(DEFENCE_ID)
    fetcher = exported(**{url: failure(url, 500)})
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, refs[0])).pgn == SACRIFICE
    with pytest.raises(FetchError):
        await extractor.fetch(fetcher, refs[1])


async def test_a_challenge_on_a_chapter_s_json_is_a_block() -> None:
    url = chapter_json_url(SACRIFICE_ID)
    challenge = failure(url, 403, CHALLENGE_PAGE)
    fetcher = exported(**{url: challenge})
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    with pytest.raises(Blocked) as exc:
        await extractor.fetch(fetcher, refs[0])
    assert exc.value is challenge


# A study whose owner turned export off: the list from its JSON, then each
# chapter's own export.


def chapter_url(study: str, chapter_id: str) -> str:
    return f"{ROOT}/api/study/{study}/{chapter_id}.pgn"


def export_off(**more: Response) -> FakeFetcher:
    """Lichess for a public study whose owner lets no one else export it."""
    return exported(
        **{
            study_url(STUDY): failure(study_url(STUDY), 403),
            study_json_url(): json_of(SACRIFICE_ID, chapters=LISTED),
            chapter_url(STUDY, SACRIFICE_ID): study_export(SACRIFICE),
            chapter_url(STUDY, DEFENCE_ID): study_export(DEFENCE),
            chapter_url(STUDY, MATE_ID): study_export(MATE),
        }
        | more
    )


async def test_a_study_with_export_off_is_read_chapter_by_chapter() -> None:
    fetcher = export_off()
    extractor = StudiesExtractor(f"{ROOT}/study/{STUDY}")
    collection, refs = await extractor.discover(fetcher)
    # Where its export would have put it, file for file.
    assert (collection.slug, collection.name) == (
        f"sacrifices-on-h7-{STUDY}",
        STUDY_NAME,
    )
    assert [(r.id, r.path) for r in refs] == [
        (SACRIFICE_ID, ("01-the-bishop-takes-on-h7",)),
        (DEFENCE_ID, ("02-the-knight-on-f6-defends",)),
        (MATE_ID, ("03-queen-and-knight-mate",)),
    ]
    # Discovery asks for the list once; each chapter is its item's own
    # requests, so a resumed run asks nothing for the chapters it already has.
    assert fetcher.urls == [study_url(STUDY), study_json_url()]
    items = [await extractor.fetch(fetcher, ref) for ref in refs]
    assert [item.pgn for item in items] == [SACRIFICE, DEFENCE, MATE]
    assert fetcher.urls[2:] == [
        chapter_url(STUDY, SACRIFICE_ID),
        chapter_json_url(SACRIFICE_ID),
        chapter_url(STUDY, DEFENCE_ID),
        chapter_json_url(DEFENCE_ID),
        chapter_url(STUDY, MATE_ID),
        chapter_json_url(MATE_ID),
    ]
    _, _, headers, _ = fetcher.requests[2]
    assert "Accept" not in headers  # as for the whole export, so 403 stays 403


async def test_a_chapter_url_of_such_a_study_keeps_its_number_in_the_study() -> None:
    fetcher = export_off()
    extractor = StudiesExtractor(f"{ROOT}/study/{STUDY}/{MATE_ID}")
    _, refs = await extractor.discover(fetcher)
    assert [(r.id, r.path) for r in refs] == [(MATE_ID, ("03-queen-and-knight-mate",))]
    assert (await extractor.fetch(fetcher, refs[0])).pgn == MATE


async def test_a_private_study_you_may_see_but_not_export_is_read() -> None:
    # Its owner keeps export to themselves; the token's account is a member.
    save_token()
    fetcher = export_off()
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    await extractor.fetch(fetcher, refs[0])
    assert all(
        h.get("Authorization") == f"Bearer {TOKEN}" for _, _, h, _ in fetcher.requests
    )


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_study_whose_json_refuses_too_keeps_lichess_s_refusal(
    status: int,
) -> None:
    fetcher = export_off(**{study_json_url(): failure(study_json_url(), status)})
    with pytest.raises(FetchError) as exc:
        await StudiesExtractor(STUDY).discover(fetcher)
    assert str(exc.value).startswith(REFUSED)
    assert exc.value.status == 403


async def test_a_bot_challenge_on_the_json_reaches_the_runner_as_a_block() -> None:
    challenge = failure(study_json_url(), 403, CHALLENGE_PAGE)
    fetcher = export_off(**{study_json_url(): challenge})
    with pytest.raises(Blocked) as exc:
        await StudiesExtractor(STUDY).discover(fetcher)
    assert exc.value is challenge


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (json_of(SACRIFICE_ID, study="Other123", chapters=LISTED), "another study"),
        (json_of(SACRIFICE_ID), "lists no chapters"),
        (
            json_of(SACRIFICE_ID, chapters=[("not an id", "Chapter")]),
            "lists a chapter without an id",
        ),
        (json_of(SACRIFICE_ID, chapters=[]), f"{SUBJECT} has no chapters"),
    ],
)
async def test_json_that_does_not_list_the_study_s_chapters_is_refused(
    answer: str, message: str
) -> None:
    fetcher = export_off(**{study_json_url(): answer})
    with pytest.raises(FetchError, match=message):
        await StudiesExtractor(STUDY).discover(fetcher)


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (
            failure(chapter_url(STUDY, DEFENCE_ID), 403),
            "refuses its chapters as well as the whole study",
        ),
        (
            failure(chapter_url(STUDY, DEFENCE_ID), 404),
            f"lichess has no chapter {DEFENCE_ID} in study {STUDY}",
        ),
        (
            failure(chapter_url(STUDY, DEFENCE_ID), 401),
            "is private; if your account may see it",
        ),
        (study_export(MATE), "came back as something else"),
        (study_export(DEFENCE, MATE), "came back as something else"),
        ("<html>Not a PGN</html>", "is not a study export|came back as something else"),
    ],
)
async def test_a_chapter_that_does_not_come_back_as_itself_fails_alone(
    answer: Response, message: str
) -> None:
    fetcher = export_off(**{chapter_url(STUDY, DEFENCE_ID): answer})
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, refs[0])).pgn == SACRIFICE
    with pytest.raises(FetchError, match=message):
        await extractor.fetch(fetcher, refs[1])


# A gamebook chapter: its hints and replies to wrong moves, from its JSON.

BOOK_ID = "Gb0ok123"
BOOK_NAME = "Find the center"
BOOK = chapter(BOOK_ID, BOOK_NAME, "1. e4 { Center. } *", ChapterMode="gamebook")
BOOK_TREE = [
    {"ply": 0, "fen": chess.STARTING_FEN, "gamebook": {"hint": "Start in the center."}},
    {
        "ply": 1,
        "id": "/?",
        "uci": "e2e4",
        "san": "e4",
        "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "comments": [{"id": "aB12", "text": "Center."}],
        "gamebook": {"deviation": "Claim the center with a pawn."},
    },
]
BOOK_ANNOTATED = BOOK.replace(
    "1. e4 { Center. } *",
    "{ Hint: Start in the center. } 1. e4 { Center. } "
    "{ When any other wrong move is played: Claim the center with a pawn. } *",
)


def gamebook_study(**more: Response) -> FakeFetcher:
    return FakeFetcher(
        {
            study_url(STUDY): study_export(SACRIFICE, BOOK),
            chapter_json_url(SACRIFICE_ID): json_of(SACRIFICE_ID),
            chapter_json_url(BOOK_ID): json_of(BOOK_ID, tree=BOOK_TREE),
        }
        | more
    )


async def test_a_gamebook_chapter_gets_its_hints_from_its_json() -> None:
    fetcher = gamebook_study()
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    items = [await extractor.fetch(fetcher, ref) for ref in refs]
    assert [item.pgn for item in items] == [SACRIFICE, BOOK_ANNOTATED]


async def test_a_gamebook_s_description_comes_before_its_first_hint() -> None:
    answer = json_of(BOOK_ID, tree=BOOK_TREE, description="Find White's moves.")
    fetcher = gamebook_study(**{chapter_json_url(BOOK_ID): answer})
    extractor = StudiesExtractor(f"{ROOT}/study/{STUDY}/{BOOK_ID}")
    _, [ref] = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, ref)).pgn == BOOK_ANNOTATED.replace(
        "{ Hint:", "{ Find White's moves. } { Hint:"
    )


async def test_the_tree_of_a_chapter_that_is_no_gamebook_is_not_read() -> None:
    # A chapter switched out of gamebook mode can keep texts Lichess no longer
    # shows; its export says which mode it is in.
    answer = json_of(SACRIFICE_ID, tree=BOOK_TREE)
    fetcher = gamebook_study(**{chapter_json_url(SACRIFICE_ID): answer})
    extractor = StudiesExtractor(STUDY)
    _, refs = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, refs[0])).pgn == SACRIFICE


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (json_of(BOOK_ID, tree="no moves"), "carries no moves"),
        (json.dumps({"study": {"id": STUDY, "chapter": {"id": BOOK_ID}}}), "no moves"),
    ],
)
async def test_a_gamebook_whose_json_carries_no_tree_fails_alone(
    answer: str, message: str
) -> None:
    # Written without its hints, it would be skipped by every later run.
    fetcher = gamebook_study(**{chapter_json_url(BOOK_ID): answer})
    extractor = StudiesExtractor(STUDY)
    _, [first, book] = await extractor.discover(fetcher)
    assert (await extractor.fetch(fetcher, first)).pgn == SACRIFICE
    with pytest.raises(FetchError, match=message):
        await extractor.fetch(fetcher, book)
