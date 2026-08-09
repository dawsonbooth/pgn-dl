"""The chess.com courses extractor."""

from __future__ import annotations

import re
from collections import Counter

import chess

from ...core.extractor import Extractor
from ...core.fetch import Fetcher
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from .client import CoursesClient
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


class CoursesExtractor(Extractor):
    key = "chesscom:courses"
    url_patterns = (re.compile(r"^https?://(www\.)?chess\.com/courses/"),)
    needs_browser = True

    def __init__(self, target: str) -> None:
        self.course_id = course_id_from(target)
        self._client: CoursesClient | None = None
        # Keyed by ItemRef.path, which is unique per item; a lesson id can repeat
        # across chapters, and keying by it would give repeats one Round header.
        self._rounds: dict[tuple[str, ...], str] = {}

    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        self._client = CoursesClient(fetcher)
        await self._client.authenticate()
        raw = await self._client.get_learning_session(self.course_id)
        info, headers = parse_course_skeleton(raw)

        chapter_idx = {cid: i for i, (cid, _) in enumerate(info.chapters, start=1)}
        chapter_name = dict(info.chapters)
        # A lesson naming a chapter the outline does not list is still a lesson,
        # and one sampled course had 41 of them out of 825. They go to a chapter
        # of their own after the listed ones, which leaves every listed
        # chapter's numbering as it was.
        unlisted = len(info.chapters) + 1

        refs: list[ItemRef] = []
        # Keyed by directory rather than chapter id, so the lessons gathered
        # under `unlisted` share one counter and cannot collide.
        seen_per_chapter: Counter[str] = Counter()
        for header in headers:
            cid = header.get("chapterId")
            number = chapter_idx.get(cid, unlisted)
            name = chapter_name.get(cid, "unlisted")
            chapter = f"{number:02d}-{slugify(name, 'chapter')}"
            seen_per_chapter[chapter] += 1
            idx = seen_per_chapter[chapter]
            lesson = slugify(header.get("name", ""), "lesson")
            ref = ItemRef(id=header["id"], path=(chapter, f"{idx:03d}-{lesson}"))
            refs.append(ref)
            self._rounds[ref.path] = f"{number}.{idx}"

        collection_headers = {"Event": info.name, "Site": "chess.com"}
        if info.author:
            collection_headers["Annotator"] = info.author

        collection = self.collection(
            slug=info.slug or info.name,
            name=info.name,
            headers=collection_headers,
        )
        return collection, refs

    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        # ``fetcher`` is unused: discover() built a client around the same one,
        # and the client carries the auth token every lesson request needs.
        if self._client is None:
            raise RuntimeError("call discover() first")
        content = parse_lesson_tree(await self._client.get_lesson(ref.id))
        headers = {"ChessComLessonId": content.id}
        if round_ := self._rounds.get(ref.path):
            headers["Round"] = round_
        return GameItem(
            headers=headers,
            starting_fen=content.starting_fen or chess.STARTING_FEN,
            comment=content.description,
            root=content.root,
        )
