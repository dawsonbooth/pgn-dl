"""Lichess studies.

A study is exported whole by Lichess's API as PGN, one game per chapter, with
its comments, variations, evaluation glyphs and drawings already in the form
pgn-dl writes everywhere else. Each chapter is written as Lichess exports it,
one file per chapter in the study's order, with what the export leaves out
added as comments.

Because nothing is rebuilt, a move python-chess cannot play loses nothing
here, unlike in a source pgn-dl writes the moves of (see ``core/pgn.py``), and
it is written as Lichess has it. Lichess's own data has such moves: Practice's
Greek Gift introduction carries an illegal ``1... Kf8`` sideline. What is
checked is that the export is a study at all.

Discovery downloads the whole study, which is a single request and the only
way the API gives its chapters' names.

The export leaves out a chapter's description, the study's own description,
and a gamebook's hints and replies to wrong moves (``gamebook.py``). All are
in lichess.org's own JSON for the chapter, which the study page's script and
Lichess's mobile app read: lila answers a request for JSON with it and takes
the API token there as it does on the API (``Study.chapter``:
``OpenOrScoped(_.Study.Read, ...)``). So each chapter is one request more, and
those texts are added to its export as comments (``comments.py``): the study's
description at the start of its first chapter, then the chapter's own, then
whatever the export starts with.

An owner can turn export off (the study's Share & export setting). Lichess
then refuses the whole-study export, but it checks the setting nowhere else:
each chapter's own export is still served to anyone who may view the study
(lila's ``Study.scala``: ``pgnWithFlags`` checks ``settings.shareable``,
``doChapterPgn`` does not). Lichess's site hides the chapter download along
with the rest of Share & export. For such a study the chapter list comes from
the study's JSON, and each chapter is a request of its own, which is the text
the whole export would have held.

A private study needs the saved token of an account that may see it (see
``auth.py``). Practice (``practice.py``) reads its studies through here too.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import chess.pgn

from ...core.extractor import Extractor
from ...core.fetch import Blocked, Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from . import auth, comments, gamebook
from .client import ROOT, Client

#: Lichess's ids: eight letters and digits, case-sensitive.
ID = r"[A-Za-z0-9]{8}"

_TARGET = re.compile(
    rf"^(?:https?://(?:www\.)?lichess\.org/study/)?(?P<study>{ID})"
    rf"(?:/(?P<chapter>{ID}))?(?:\.pgn)?/?$"
)
_CHAPTER_URL = re.compile(rf"/study/{ID}/({ID})$")

TOKEN_REFUSED = "Lichess refused the saved token; run: pgn-dl login lichess"


@dataclass(frozen=True)
class Chapter:
    id: str
    name: str
    study_name: str
    text: str  # the chapter's game, as Lichess exported it


@dataclass(frozen=True)
class Listed:
    """A chapter as its study lists it."""

    id: str
    name: str


def parse_target(target: str) -> tuple[str, str | None]:
    """A study URL, a chapter URL, or a bare study id, as (study, chapter)."""
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET.match(cleaned)
    if m is None:
        raise ValueError(f"could not read a lichess study from {target!r}")
    return m.group("study"), m.group("chapter")


class _Quiet(chess.pgn.GameBuilder):
    """Keeps a move python-chess cannot play in ``errors`` without logging it.

    The text is written as Lichess sent it either way, so the error is not one
    to report, and logged it lands in the middle of the run's progress.
    """

    def handle_error(self, error: Exception) -> None:
        self.game.errors.append(error)


def read_chapters(pgn: str, *, subject: str) -> list[Chapter]:
    """Every game of a study export, each with its own text, in order.

    The text is sliced out of the export rather than written again, so a file
    holds what Lichess sent. Every game must be a study chapter: an export
    always names its study, and anything else (an error page, say) is refused.
    """
    stream = io.StringIO(pgn)
    chapters = []
    while True:
        start = stream.tell()
        game = chess.pgn.read_game(stream, Visitor=_Quiet)
        if game is None:
            break
        text = pgn[start : stream.tell()].strip()
        headers = game.headers
        if "StudyName" not in headers:
            if "BroadcastURL" in headers:
                raise FetchError(
                    f"{subject} is a broadcast round, which pgn-dl does not "
                    "download as a study"
                )
            raise FetchError(f"{subject} is not a study export")
        if not text.endswith(("*", "1-0", "0-1", "1/2-1/2")):
            # Every chapter ends in its result; one that does not was cut off.
            raise FetchError(f"{subject} came back cut short")
        m = _CHAPTER_URL.search(headers.get("ChapterURL", ""))
        chapters.append(
            Chapter(
                id=m.group(1) if m else str(len(chapters) + 1),
                name=headers.get("ChapterName") or headers.get("Event", ""),
                study_name=headers.get("StudyName", ""),
                text=text,
            )
        )
    return chapters


def _unauthorized(subject: str, client: Client, *, sent_token: bool) -> FetchError:
    """Lichess's 401: the token it was sent is refused, or none was sent and
    ``subject`` is private."""
    if sent_token and client.signed_in:
        return FetchError(TOKEN_REFUSED, status=401)
    return FetchError(
        f"{subject} is private; if your account may see it, run: pgn-dl login lichess",
        status=401,
    )


async def export(client: Client, study: str, *, send_token: bool = True) -> str:
    """A study's PGN export, with Lichess's refusals put in words.

    It is asked for with no Accept header: with a token, Lichess answers a
    refusal in JSON only, and to a request that accepts only PGN it answers
    406 ("This endpoint only returns JSON") in place of the 403 that says why.
    """
    url = f"{ROOT}/api/study/{study}.pgn"
    subject = f"lichess study {study}"
    try:
        return await client.text(url, auth=send_token)
    except Blocked:
        raise
    except FetchError as exc:
        if exc.status == 401:
            raise _unauthorized(subject, client, sent_token=send_token) from None
        if exc.status == 403:
            # Lichess refuses alike a study that is private and one whose
            # owner's Share & export setting leaves this viewer out, and calls
            # both "private"; the second can be open to view in any browser.
            advice = (
                "the saved token's account is not one of them"
                if client.signed_in and send_token
                else "if yours is one, run: pgn-dl login lichess"
            )
            raise FetchError(
                f"{subject} cannot be downloaded: it is private, or its owner "
                f"lets only some accounts export it; {advice}",
                status=403,
            ) from None
        if exc.status == 404:
            raise FetchError(_missing(study), status=404) from None
        raise


async def chapter_export(
    client: Client, study: str, chapter: str, *, send_token: bool = True
) -> str:
    """One chapter's own export, as Lichess wrote it."""
    url = f"{ROOT}/api/study/{study}/{chapter}.pgn"
    subject = f"lichess study {study} chapter {chapter}"
    try:
        text = await client.text(url, auth=send_token)
    except Blocked:
        raise
    except FetchError as exc:
        if exc.status == 401:
            raise _unauthorized(subject, client, sent_token=send_token) from None
        if exc.status == 403:
            raise FetchError(
                f"{subject} cannot be downloaded: Lichess refuses its chapters "
                "as well as the whole study",
                status=403,
            ) from None
        if exc.status == 404:
            raise FetchError(_missing(study, chapter), status=404) from None
        raise
    chapters = read_chapters(text, subject=subject)
    if [c.id for c in chapters] != [chapter]:
        raise FetchError(f"{subject} came back as something else")
    return chapters[0].text


async def study_json(
    client: Client, study: str, chapter: str | None = None, *, send_token: bool = True
) -> dict[str, Any]:
    """lichess.org's JSON for a study, listing its chapters, or for a chapter.

    Checked to be the study, and the chapter, that was asked for.
    """
    if chapter is None:
        url = f"{ROOT}/study/{study}?chapters=true"
        subject = f"lichess study {study}"
    else:
        url = f"{ROOT}/study/{study}/{chapter}"
        subject = f"lichess study {study} chapter {chapter}"
    missing = _missing(study, chapter)
    data = await _study_json(client, url, subject, missing, send_token=send_token)
    if chapter is not None and _shown(data) != chapter:
        # A study left idle with its position away from a chapter's start is
        # rewound to its first chapter by the next view, and that view gets
        # the first chapter (lila's ``maybeResetAndGetChapter``). Asked again,
        # Lichess answers with the chapter asked for.
        data = await _study_json(client, url, subject, missing, send_token=send_token)
    found = data.get("study") if isinstance(data, dict) else None
    if not isinstance(found, dict) or found.get("id") != study:
        raise FetchError(f"lichess answered for another study than {subject}")
    if chapter is not None and _shown(data) != chapter:
        raise FetchError(f"lichess answered with another chapter than {subject}")
    return data


def _shown(data: object) -> object:
    """The id of the chapter a study's JSON is for."""
    found = data.get("study") if isinstance(data, dict) else None
    shown = found.get("chapter") if isinstance(found, dict) else None
    return shown.get("id") if isinstance(shown, dict) else None


def _missing(study: str, chapter: str | None = None) -> str:
    """What Lichess's 404 for ``study``, or for one of its chapters, means."""
    if chapter is None:
        return f"lichess has no study {study}"
    return f"lichess has no chapter {chapter} in study {study}"


async def _study_json(
    client: Client, url: str, subject: str, missing: str, *, send_token: bool
) -> Any:
    try:
        return await client.json(url, auth=send_token)
    except Blocked:
        raise
    except FetchError as exc:
        if exc.status == 401:
            raise _unauthorized(subject, client, sent_token=send_token) from None
        if exc.status == 403:
            raise FetchError(
                f"{subject} is private, and the saved token's account may not see it",
                status=403,
            ) from None
        if exc.status == 404:
            raise FetchError(missing, status=404) from None
        raise


async def chapter_list(
    client: Client, study: str, *, refusal: FetchError, send_token: bool = True
) -> tuple[str, list[Listed]]:
    """The name and chapters of a study whose export ``refusal`` refused.

    Refused here too, the study is private to this viewer, which Lichess's
    first refusal says best.
    """
    subject = f"lichess study {study}"
    try:
        data = await study_json(client, study, send_token=send_token)
    except Blocked:
        raise
    except FetchError as exc:
        if exc.status in (401, 403, 404):
            raise refusal from None
        raise
    found = data["study"]
    chapters = found.get("chapters")
    if not isinstance(chapters, list):
        raise FetchError(f"{subject} lists no chapters")
    listed = []
    for entry in chapters:
        chapter = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(chapter, str) or not re.fullmatch(ID, chapter):
            raise FetchError(f"{subject} lists a chapter without an id")
        name = entry.get("name")
        listed.append(Listed(chapter, name if isinstance(name, str) else ""))
    name = found.get("name")
    return (name if isinstance(name, str) else ""), listed


async def chapters_of(
    client: Client, study: str, *, send_token: bool = True
) -> tuple[str, list[Listed], dict[str, str]]:
    """A study's name, its chapters in order, and their texts by id.

    The texts are there when the whole study could be exported; when its
    owner turned export off there are none, and each chapter is asked for on
    its own.
    """
    subject = f"lichess study {study}"
    try:
        text = await export(client, study, send_token=send_token)
    except FetchError as exc:
        if exc.status != 403 or isinstance(exc, Blocked):
            raise
        name, listed = await chapter_list(
            client, study, refusal=exc, send_token=send_token
        )
        return name, listed, {}
    exported = read_chapters(text, subject=subject)
    name = exported[0].study_name if exported else ""
    listed = [Listed(c.id, c.name) for c in exported]
    return name, listed, {c.id: c.text for c in exported}


#: What lila keeps for a description switched on but never written; its
#: page shows nothing for it.
_UNWRITTEN = "-"


def descriptions(data: dict[str, Any], *, first: bool) -> list[str]:
    """What a chapter's JSON says to show before its moves: the study's own
    description, on its first chapter and only if the study shows it, then
    the chapter's."""
    found = data["study"]
    texts = []
    features = found.get("features")
    shown = isinstance(features, dict) and features.get("description") is True
    if first and shown:
        texts.append(found.get("description"))
    texts.append(found["chapter"].get("description"))
    texts = [t.strip() for t in texts if isinstance(t, str)]
    return [t for t in texts if t and t != _UNWRITTEN]


async def finish(
    client: Client,
    study: str,
    chapter: str,
    text: str,
    *,
    first: bool,
    intro: Sequence[str] = (),
    send_token: bool = True,
) -> str:
    """A chapter's export, with what it leaves out added from its JSON.

    ``first`` says whether it is the study's first chapter, which carries the
    study's description; ``intro`` goes before everything.
    """
    data = await study_json(client, study, chapter, send_token=send_token)
    subject = f"lichess study {study} chapter {chapter}"
    notes: list[comments.Note] = []
    if gamebook.is_gamebook(text):
        analysis = data.get("analysis")
        parts = analysis.get("treeParts") if isinstance(analysis, dict) else None
        notes = gamebook.notes(parts, subject)
    texts = [*intro, *descriptions(data, first=first)]
    return comments.annotate(text, notes, subject, intro=texts)


async def whole_study(
    client: Client,
    study: str,
    *,
    intro: Sequence[str] = (),
    send_token: bool = True,
) -> str:
    """Every chapter of a study in its order, each finished, as one text.

    The chapters are joined as the export joins them, so a study that exports
    whole comes out as its export with the additions and nothing else.
    ``intro`` goes before everything in the first chapter.
    """
    _, listed, texts = await chapters_of(client, study, send_token=send_token)
    if not listed:
        raise FetchError(f"lichess study {study} has no chapters")
    finished = []
    for n, chapter in enumerate(listed):
        text = texts.get(chapter.id)
        if text is None:
            text = await chapter_export(
                client, study, chapter.id, send_token=send_token
            )
        finished.append(
            await finish(
                client,
                study,
                chapter.id,
                text,
                first=n == 0,
                intro=intro if n == 0 else (),
                send_token=send_token,
            )
        )
    return "\n\n\n".join(finished)


@dataclass
class _Context:
    client: Client
    #: Each chapter's text, by id, when the whole study was exported; empty
    #: when each chapter is a request of its own.
    texts: dict[str, str]
    #: The study's first chapter, which carries the study's description.
    first: str


class StudiesExtractor(Extractor[_Context]):
    """Every chapter of a study, or one."""

    key = "lichess:studies"
    url_patterns = (re.compile(rf"^https?://(?:www\.)?lichess\.org/study/{ID}\b"),)

    def __init__(self, target: str) -> None:
        super().__init__()
        self.study, self.chapter = parse_target(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        client = Client(fetcher, token=auth.saved_token())
        subject = f"lichess study {self.study}"
        name, listed, texts = await chapters_of(client, self.study)
        if not listed:
            raise FetchError(f"{subject} has no chapters")
        # Numbered from the whole study, so one chapter asked for by URL lands
        # where downloading the study puts it, and a study read chapter by
        # chapter lands where its export would have.
        placed = [
            (chapter, f"{n:02d}-{slugify(chapter.name, 'chapter')}")
            for n, chapter in enumerate(listed, start=1)
        ]
        if self.chapter is not None:
            placed = [(c, path) for c, path in placed if c.id == self.chapter]
            if not placed:
                raise FetchError(_missing(self.study, self.chapter))
        name = name or self.study
        # The id keeps two studies of one name apart.
        collection = self.collection(
            slug=f"{slugify(name, 'study')}-{self.study}", name=name
        )
        refs = [ItemRef(id=c.id, path=(path,)) for c, path in placed]
        return collection, refs, _Context(client, texts, listed[0].id)

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        text = context.texts.get(ref.id)
        if text is None:
            text = await chapter_export(context.client, self.study, ref.id)
        first = ref.id == context.first
        text = await finish(context.client, self.study, ref.id, text, first=first)
        return GameItem(pgn=text)
