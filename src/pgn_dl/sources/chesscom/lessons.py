"""chess.com lessons.

Unlike chess.com Courses (the paid product, ``sources.chesscom.courses``),
lessons need no RPC and no wire-format parsing: the lesson page embeds the whole
lesson as an HTML-entity-encoded JSON attribute whose ``questions`` are already
PGN strings.

Headers and drawings are what get rewritten. Upstream every question is
``[Event "?"]``, so the lesson course and lesson identity are substituted in and
a downloaded file says where it came from. chess.com also writes its own
``%c_arrow`` / ``%c_highlight`` commands with URL-encoded values, which no GUI
understands; those become the standard ``%cal`` / ``%csl`` that the courses path
already emits.

``%hint`` and ``%wrong`` have no standard equivalent and are left as literal
comment text.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import unquote

import chess.pgn

from ...core.extractor import Extractor
from ...core.fetch import Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from ...core.pgn import reexport_pgn

BASE = "https://www.chess.com/lessons"
LEARN_URL = "https://www.chess.com/learn"

LESSON_ATTR_RE = re.compile(r"data-lesson='([^']*)'")
_LEARN_PATH_RE = re.compile(r"userLearnPath\s*=\s*JSON\.parse\('(.*?)'\)\s*;")

_ARROW_RE = re.compile(r"\[%c_arrow ([^\]]*)\]")
_HIGHLIGHT_RE = re.compile(r"\[%c_highlight ([^\]]*)\]")

# chess.com uses one accent colour per drawing; map to the PGN colour letters
# the courses path already uses. Anything unrecognised falls back to green.
_COLOR_LETTERS = (
    ("ffaa00", "Y"),
    ("255,170,0", "Y"),
    ("ff0000", "R"),
    ("255,0,0", "R"),
    ("f00", "R"),
)


def extract_lesson(page_html: str) -> dict:
    """Decode the ``data-lesson`` attribute of a lesson page.

    A 200 with no ``data-lesson`` is what an expired session looks like:
    chess.com serves the login wall with no HTTP error. Hence
    :class:`FetchError` rather than ``ValueError``, so the runner tallies it as
    a per-item failure and leaves the item for a re-run.
    """
    m = LESSON_ATTR_RE.search(page_html)
    if m is None:
        raise FetchError(
            "no data-lesson attribute on the page; not a lesson page, or the "
            "session has expired and chess.com served a login wall"
        )
    return json.loads(html.unescape(m.group(1)))


def course_lesson_slugs(page_html: str, course: str) -> list[str]:
    """Lesson slugs of a lesson course page, in document (curriculum) order.

    Links repeat across nav and cards, so de-duplicate while preserving
    order. Do not sort: the page order IS the curriculum order.
    """
    pattern = re.compile(rf"/lessons/{re.escape(course)}/([a-z0-9-]+)")
    out: list[str] = []
    for m in pattern.finditer(page_html):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def extract_learn_path(page_html: str) -> dict:
    """Decode ``window.chesscom.learnGuide.userLearnPath`` from the /learn page.

    Some lesson courses (chess.com's guided, Duolingo-style learn path) have
    no landing page of their own; this is where their curriculum lives
    instead. The blob is JS-escaped using only ``\\uXXXX`` sequences (no literal
    quotes, no ``\\'``, verified against a live capture), so it decodes cleanly
    by first treating it as a JSON string body. Do not use
    ``codecs.decode(s, "unicode_escape")``: that round-trips through latin-1
    and corrupts non-ASCII lesson titles.
    """
    m = _LEARN_PATH_RE.search(page_html)
    if m is None:
        raise FetchError(
            "no userLearnPath blob on the /learn page; not a learn page, or "
            "the session has expired and chess.com served a login wall"
        )
    return json.loads(json.loads('"' + m.group(1) + '"'))


def learn_path_course(payload: dict, course: str) -> tuple[str, list[str]] | None:
    """(lesson-course title, ordered lesson slugs) for `course`, or None."""
    for section in payload.get("sections", []):
        for c in section.get("courses", []):
            if c.get("path") == course:
                slugs = [
                    path
                    for lesson in c.get("lessons", [])
                    if (path := lesson.get("path"))
                ]
                return c.get("name", ""), slugs
    return None


def _color_letter(spec: str) -> str:
    decoded = unquote(spec).replace(" ", "").lower()
    for needle, letter in _COLOR_LETTERS:
        if needle in decoded:
            return letter
    return "G"


def _drawing_fields(spec: str) -> dict[str, str]:
    """chess.com packs a drawing as ``key;value;key;value`` after a leading id."""
    parts = unquote(spec).split(";")
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _arrow_sub(m: re.Match[str]) -> str:
    items = []
    for spec in m.group(1).split(","):
        f = _drawing_fields(spec)
        if (a := f.get("from")) and (b := f.get("to")):
            items.append(f"{_color_letter(spec)}{a}{b}")
    return f"[%cal {','.join(items)}]" if items else ""


def _highlight_sub(m: re.Match[str]) -> str:
    items = []
    for spec in m.group(1).split(","):
        f = _drawing_fields(spec)
        if sq := f.get("square"):
            items.append(f"{_color_letter(spec)}{sq}")
    return f"[%csl {','.join(items)}]" if items else ""


def translate_drawings(comment: str) -> str:
    """Rewrite chess.com drawing commands as standard %cal / %csl."""
    out = _ARROW_RE.sub(_arrow_sub, comment)
    return _HIGHLIGHT_RE.sub(_highlight_sub, out)


def _translate_node_comments(node: chess.pgn.GameNode) -> None:
    if node.comment:
        node.comment = translate_drawings(node.comment)
    if node.starting_comment:
        node.starting_comment = translate_drawings(node.starting_comment)
    for child in node.variations:
        _translate_node_comments(child)


def question_to_pgn(question: str, headers: dict[str, str]) -> str:
    """Re-export one question's PGN with our headers and standard drawings."""
    return reexport_pgn(
        question,
        headers,
        subject="question",
        error=ValueError,
        transform=_translate_node_comments,
    )


def lesson_to_pgn(payload: dict, headers: dict[str, str]) -> str:
    """Every question of a lesson, as one PGN file of N games."""
    base = dict(headers)
    base["ChessComLessonId"] = payload.get("id", "")
    base["LessonName"] = payload.get("name", "")
    games = []
    for i, question in enumerate(payload.get("questions", []), start=1):
        games.append(question_to_pgn(question, {**base, "Round": str(i)}))
    if not games:
        # Returning "" would have the runner write a 1-byte file, count it as
        # written, and skip it on every later run because it exists.
        name = payload.get("path") or base["ChessComLessonId"] or "?"
        raise ValueError(f"lesson {name!r} has no questions; nothing to export")
    return "\n\n".join(games)


_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')

# A full URL must name /lessons/: with every prefix part optional,
# https://www.chess.com/courses/<slug> read as the lesson course "courses" and
# the subcommand would then GET /lessons/courses/<slug>.
_TARGET_RE = re.compile(
    r"^(?:https?://(?:www\.)?chess\.com/lessons/|/?(?:lessons/)?)"
    r"(?P<course>[a-z0-9-]+)(?:/(?P<lesson>[a-z0-9-]+))?/?$",
    re.IGNORECASE,
)


def parse_target(s: str) -> tuple[str, str | None]:
    """Accept a lessons URL or a bare ``lesson-course`` / ``lesson-course/lesson``."""
    # chess.com's own share links carry a ?ref=... query, and in-page links a
    # #fragment. Neither addresses the lesson, and the pattern is end-anchored.
    target = s.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET_RE.match(target)
    if not m:
        raise ValueError(f"could not read a lesson course/lesson from {s!r}")
    return m.group("course").lower(), (m.group("lesson") or "").lower() or None


class LessonsExtractor(Extractor):
    """One lesson, or every lesson of a lesson course.

    A lesson course is chess.com's free ``/lessons/<slug>`` curriculum, not the
    paid Courses product; that one is ``chesscom:courses``.
    """

    key = "chesscom:lessons"
    url_patterns = (re.compile(r"^https?://(www\.)?chess\.com/lessons/"),)
    needs_browser = True

    def __init__(self, target: str) -> None:
        self.course, self.lesson = parse_target(target)
        # The lesson-course title, once a listing has supplied one. Empty until
        # then, and in single-lesson mode possibly until fetch() reads the
        # lesson's own course block, so every reader falls back to the slug.
        self._title = ""

    def _pgn_headers(self) -> dict[str, str]:
        return {"Event": self._title or self.course, "Site": "chess.com"}

    async def _course_slugs(self, fetcher: Fetcher) -> list[str] | None:
        """Ordered lesson slugs.

        None when there is no usable lesson-course landing page AND the
        lesson course is absent from chess.com's guided learn path too.
        """
        try:
            page = await fetcher.get_text(f"{BASE}/{self.course}", headers={})
        except FetchError:
            page = None
        if page is not None:
            slugs = course_lesson_slugs(page, self.course)
            if slugs:
                if m := _OG_TITLE_RE.search(page):
                    self._title = m.group(1)
                return slugs
        # No landing page, or one that lists no lesson (an expired session's
        # login wall, or a layout change). Both get the same answer, and no
        # title is taken off such a page. Fall back to the learn path: some
        # lesson courses, e.g. advanced-chess-tactics, are only ever reachable
        # through it and never through a landing page.
        try:
            learn_page = await fetcher.get_text(LEARN_URL, headers={})
            found = learn_path_course(extract_learn_path(learn_page), self.course)
        except FetchError:
            return None
        if found is None:
            return None
        title, slugs = found
        if not slugs:
            # Present in the learn path but carrying no lesson: same failure as
            # a landing page that lists none, and no title taken from it either.
            return None
        self._title = title
        return slugs

    async def discover(self, fetcher: Fetcher) -> tuple[Collection, list[ItemRef]]:
        # Numbering always comes from the lesson-course page, in both modes, so
        # that downloading one lesson and downloading the whole lesson course
        # agree on paths and resume across each other.
        slugs = await self._course_slugs(fetcher)

        if self.lesson is None:
            if slugs is None:
                raise FetchError(
                    f"no lesson-course landing page and no learn path entry for "
                    f"{self.course!r}; chess.com does not publish a landing page "
                    "for every lesson course, and this one is not in the learn "
                    "path either. Download an individual lesson instead, e.g. "
                    f"{BASE}/{self.course}/<lesson>"
                )
            chosen = [(f"{i:03d}-{slug}", slug) for i, slug in enumerate(slugs, start=1)]
        else:
            if slugs is not None and self.lesson in slugs:
                idx = slugs.index(self.lesson) + 1
                chosen = [(f"{idx:03d}-{self.lesson}", self.lesson)]
            else:
                # Unnumbered: there is no usable lesson-course page, or there is
                # one and it does not list this lesson. Nothing collides within
                # a run, since a whole-lesson-course download numbers only the
                # lessons in `slugs`. Across time it can: if chess.com later
                # adds this lesson to the listing, an earlier unnumbered file
                # and a later numbered one coexist and the lesson is downloaded
                # twice. There is no position to invent for a lesson the listing
                # omits.
                chosen = [(self.lesson, self.lesson)]

        collection = self.collection(
            slug=self.course,
            name=self._title or self.course,
            headers=self._pgn_headers(),
        )
        # ``ItemRef.id`` is the lesson slug, not a UUID: the slug is what
        # addresses the page. The lesson's UUID goes into the PGN instead.
        refs = [ItemRef(id=slug, path=(slugify(name),)) for name, slug in chosen]
        return collection, refs

    async def fetch(self, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        payload = extract_lesson(
            await fetcher.get_text(f"{BASE}/{self.course}/{ref.id}", headers={})
        )
        # Single-lesson mode reaches no listing that carries the lesson-course
        # title, so the lesson's own course block is where it comes from. Only
        # that mode leaves ``_title`` empty here, and it fetches one item, so
        # nothing races for this.
        if not self._title:
            self._title = payload.get("course", {}).get("title", "")
        return GameItem(pgn=lesson_to_pgn(payload, self._pgn_headers()))
