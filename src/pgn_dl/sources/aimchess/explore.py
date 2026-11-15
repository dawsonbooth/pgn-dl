"""aimchess Explore lessons.

Explore is a library of lessons under topics (Endgame, Middlegame, ...). A
lesson is a series of steps, tutorials and exercises, each a complete PGN, and
is written as one file with a game per step. Topics are numbered directories in
the site's tab order, and lessons are numbered within their topic in the order
the site lists them. A single-lesson download is numbered from the same
listing, so it lands where a full download would put it.

Fetching a lesson opens it, which marks it started in the account's progress.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from . import auth
from .client import Client, DailyLimitReached
from .pgn import SITE, rewrite

#: The Explore page's title.
COLLECTION_NAME = "Explore Chess"

#: The status of a lesson that is announced but not published. It can't be
#: opened, but it keeps its number, so publishing it later moves no file.
COMING_SOON = "coming soon"

#: The Explore page (every lesson), a lesson page or one of the app's pages
#: under it, or a bare lesson id.
_TARGET_RE = re.compile(
    r"^(?:https?://(?:www\.)?aimchess\.com/explore"
    r"(?:/(?P<url_id>\d+)(?:/(?:analysis|done|limit))?)?/?"
    r"|(?P<bare_id>\d+))$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Lesson:
    id: str
    name: str
    topic: str
    section: str  # "" for the lessons the site files under no section
    status: str


@dataclass(frozen=True)
class Step:
    type: str  # "tutorial" or "exercise"
    pgn: str


def lesson_id_from(target: str) -> str | None:
    """The lesson id in a lesson URL or bare id; None for the Explore page."""
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET_RE.match(cleaned)
    if m is None:
        raise ValueError(f"could not read an aimchess Explore lesson from {target!r}")
    found = m.group("url_id") or m.group("bare_id")
    return str(int(found)) if found else None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def parse_listing(data: Mapping[str, Any]) -> tuple[list[str], list[Lesson]]:
    """The listing's topic names in tab order, and its lessons in site order.

    A listing whose topics or lesson ids can't be read is refused: the topic
    order numbers the directories and the ids address the lessons, so a guess
    at either could move every file on a later run.
    """
    topics, lessons = data.get("topics"), data.get("lessons")
    if not isinstance(topics, list) or not isinstance(lessons, list):
        raise FetchError("aimchess Explore listing has no topics or lessons array")
    if lessons and not topics:
        raise FetchError("aimchess Explore listing has lessons but no topics")
    names = []
    for topic in topics:
        name = topic.get("name") if isinstance(topic, Mapping) else None
        if not isinstance(name, str):
            raise FetchError("aimchess Explore listing has a topic without a name")
        names.append(name)
    parsed = []
    for entry in lessons:
        lesson_id = entry.get("id") if isinstance(entry, Mapping) else None
        if not isinstance(lesson_id, int):
            raise FetchError("aimchess Explore listing has a lesson without an id")
        parsed.append(
            Lesson(
                id=str(lesson_id),
                name=_text(entry.get("name")),
                topic=_text(entry.get("topic")),
                section=_text(entry.get("section")),
                status=_text(entry.get("status")),
            )
        )
    return names, parsed


def parse_steps(data: object, lesson_id: str) -> list[Step]:
    """A lesson's steps.

    A step without a PGN fails the whole lesson: a file written a game short
    would be skipped as done by every later run.
    """
    steps = data.get("steps_data") if isinstance(data, Mapping) else None
    if not isinstance(steps, list) or not steps:
        raise FetchError(f"aimchess lesson {lesson_id} has no steps")
    parsed = []
    for number, step in enumerate(steps, start=1):
        pgn = step.get("pgn") if isinstance(step, Mapping) else None
        if not isinstance(pgn, str) or not pgn.strip():
            raise FetchError(f"aimchess lesson {lesson_id} step {number} has no PGN")
        parsed.append(Step(type=_text(step.get("step_type")), pgn=pgn))
    return parsed


def layout(
    topics: list[str], lessons: list[Lesson]
) -> list[tuple[Lesson, tuple[str, str]]]:
    """Each published lesson in listing order, with its output path.

    A lesson whose topic isn't one of the tabs gets a directory after them.
    """
    unknown = [t for t in dict.fromkeys(x.topic for x in lessons) if t not in topics]
    directories = {
        topic: f"{number:02d}-{slugify(topic, 'topic')}"
        for number, topic in enumerate([*topics, *unknown], start=1)
    }
    counts: Counter[str] = Counter()
    placed = []
    for lesson in lessons:
        directory = directories[lesson.topic]
        counts[directory] += 1
        if lesson.status != COMING_SOON:
            name = f"{counts[directory]:03d}-{slugify(lesson.name, 'lesson')}"
            placed.append((lesson, (directory, name)))
    return placed


def lesson_to_pgn(lesson: Lesson, steps: list[Step]) -> str:
    """A lesson as PGN text with one game per step.

    Each step's player is the side the site shows the board from: the side
    that replies to the first move, since an exercise opens with the
    opponent's move.
    """
    games = []
    for number, step in enumerate(steps, start=1):
        headers = {
            "Event": lesson.name,
            "Site": SITE,
            "Round": str(number),
            "AimchessLessonId": lesson.id,
            "AimchessTopic": lesson.topic,
        }
        if lesson.section:
            headers["AimchessSection"] = lesson.section
        headers["AimchessStepType"] = step.type
        games.append(
            rewrite(
                step.pgn,
                headers,
                subject=f"aimchess lesson {lesson.id} step {number}",
                player=lesson.name,
            )
        )
    return "\n\n".join(games)


@dataclass
class _Context:
    client: Client
    lessons: dict[str, Lesson]
    # Set when aimchess refuses a lesson for the daily limit. It would refuse
    # the rest of the run too, so they fail without a request.
    limit_reached: bool = False


class ExploreExtractor(Extractor[_Context]):
    """Every Explore lesson, or one."""

    key = "aimchess:explore"
    url_patterns = (
        re.compile(
            r"^https?://(www\.)?aimchess\.com/explore(?:[/?#]|$)", re.IGNORECASE
        ),
    )

    def __init__(self, target: str) -> None:
        super().__init__()
        self.lesson_id = lesson_id_from(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], _Context]:
        client = Client(fetcher, token=auth.load_token())
        topics, lessons = parse_listing(await client.explore_listing())
        placed = layout(topics, lessons)
        if self.lesson_id is not None:
            placed = [(x, path) for x, path in placed if x.id == self.lesson_id]
            if not placed:
                raise FetchError(
                    f"aimchess lesson {self.lesson_id} is not a published Explore lesson"
                )
        refs = [ItemRef(id=x.id, path=path) for x, path in placed]
        collection = self.collection(
            slug=slugify(COLLECTION_NAME), name=COLLECTION_NAME
        )
        return collection, refs, _Context(client, {x.id: x for x, _ in placed})

    async def _fetch(
        self, context: _Context, fetcher: Fetcher, ref: ItemRef
    ) -> GameItem:
        if context.limit_reached:
            raise FetchError(
                "not requested: aimchess refused an earlier lesson in this run "
                "for the daily limit"
            )
        try:
            data = await context.client.explore_lesson(ref.id)
        except DailyLimitReached:
            context.limit_reached = True
            raise
        lesson = context.lessons[ref.id]
        return GameItem(pgn=lesson_to_pgn(lesson, parse_steps(data, ref.id)))
