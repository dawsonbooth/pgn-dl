"""The chess.com courses extractor, and what it shares with opening courses."""

from __future__ import annotations

import re
from abc import abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import ClassVar, NamedTuple

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from .client import COURSES, CoursesClient, Service
from .parser import parse_course_skeleton, parse_lesson_tree

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def course_id_from(s: str) -> str:
    """Accept a bare UUID or any chess.com/courses URL containing one."""
    m = _UUID_RE.search(s)
    if not m:
        raise ValueError(f"could not find a course UUID in {s!r}")
    return m.group(0).lower()


@dataclass
class _Context:
    client: CoursesClient
    # Keyed by ItemRef.path, which is unique per item; a lesson id can repeat
    # across chapters, and keying by it would give repeats one Round header.
    rounds: dict[tuple[str, ...], str] = field(default_factory=dict)


class Course(NamedTuple):
    """Which course to download, and the name to give it."""

    id: str
    name: str = ""  # empty uses the name from the course outline


class CoursesServiceExtractor(Extractor[_Context]):
    """Base for extractors over a deployment of chess.com's courses service.

    Subclasses set ``service`` (see ``client.py``) and implement ``_course`` to
    turn their target into a course id. Discovery and fetching are shared.
    """

    needs_browser = True
    service: ClassVar[Service]

    @abstractmethod
    async def _course(self, client: CoursesClient) -> Course:
        """Resolve the target to a course, through ``client`` if it needs a lookup."""

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        client = CoursesClient(fetcher, self.service)
        await client.authenticate()
        course = await self._course(client)
        raw = await client.get_learning_session(course.id)
        info, headers = parse_course_skeleton(raw)
        name = course.name or info.name

        chapter_idx = {cid: i for i, (cid, _) in enumerate(info.chapters, start=1)}
        chapter_name = dict(info.chapters)
        # A lesson naming a chapter the outline does not list is still a lesson,
        # and one sampled course had 41 of them out of 825. They go to a chapter
        # of their own after the listed ones, which leaves every listed
        # chapter's numbering as it was.
        unlisted = len(info.chapters) + 1

        context = _Context(client=client)
        refs: list[ItemRef] = []
        # Keyed by directory rather than chapter id, so the lessons gathered
        # under `unlisted` share one counter and cannot collide.
        seen_per_chapter: Counter[str] = Counter()
        for header in headers:
            cid = header.get("chapterId")
            number = chapter_idx.get(cid, unlisted)
            chapter_title = chapter_name.get(cid, "unlisted")
            chapter = f"{number:02d}-{slugify(chapter_title, 'chapter')}"
            seen_per_chapter[chapter] += 1
            idx = seen_per_chapter[chapter]
            lesson = slugify(header.get("name", ""), "lesson")
            ref = ItemRef(id=header["id"], path=(chapter, f"{idx:03d}-{lesson}"))
            refs.append(ref)
            context.rounds[ref.path] = f"{number}.{idx}"

        collection_headers = {"Event": name, "Site": "chess.com"}
        if info.author:
            collection_headers["Annotator"] = info.author

        collection = self.collection(
            slug=info.slug or info.name,
            name=name,
            headers=collection_headers,
        )
        return collection, refs, context

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        # ``fetcher`` is unused: discovery built a client around the same one,
        # and the client carries the auth token every lesson request needs.
        content = parse_lesson_tree(await context.client.get_lesson(ref.id))
        headers = {"ChessComLessonId": content.id}
        if round_ := context.rounds.get(ref.path):
            headers["Round"] = round_
        return GameItem(
            headers=headers,
            starting_fen=content.starting_fen or chess.STARTING_FEN,
            comment=content.description,
            root=content.root,
        )


class CoursesExtractor(CoursesServiceExtractor):
    """A chess.com Course, by the UUID in its URL."""

    key = "chesscom:courses"
    url_patterns = (re.compile(r"^https?://(www\.)?chess\.com/courses/"),)
    service = COURSES

    def __init__(self, target: str) -> None:
        super().__init__()
        self.course_id = course_id_from(target)

    async def _course(self, client: CoursesClient) -> Course:
        return Course(id=self.course_id)
