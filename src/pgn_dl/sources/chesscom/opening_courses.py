"""chess.com opening courses, the courses under chess.com/openings/courses.

These are ordinary courses served from their own deployment of the courses
service (see ``client.py``). A course page is addressed by slug,
``/openings/courses/<slug>``, and the service maps the slug to a course id.

Several openings have one course per side under the same name: both Caro-Kann
courses are "Caro-Kann Defense". The side is appended to the name so the two
downloads get different Event headers.
"""

from __future__ import annotations

import re

from ...core.fetch import FetchError, UnknownTarget
from .client import OPENING_COURSES, CoursesClient
from .courses import Course, CoursesServiceExtractor

# The URL prefix is optional so a bare slug works, but all or nothing: with the
# host optional on its own, another site's /openings/courses/ would pass.
_TARGET_RE = re.compile(
    r"^(?:https?://(?:www\.)?chess\.com/openings/courses/)?"
    r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/?$",
    re.IGNORECASE,
)

_SIDES = {"COLOR_WHITE": "White", "COLOR_BLACK": "Black"}


def slug_from(target: str) -> str:
    """The slug in an opening course's page URL, or a bare slug."""
    # Share links carry a ?query and in-page links a #fragment; neither
    # addresses the course, and the pattern is end-anchored.
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET_RE.match(cleaned)
    if m is None:
        raise ValueError(f"could not read an opening course slug from {target!r}")
    return m.group("slug").lower()


class OpeningCoursesExtractor(CoursesServiceExtractor):
    """One opening course, by the slug of its page."""

    key = "chesscom:opening-courses"
    url_patterns = (re.compile(r"^https?://(www\.)?chess\.com/openings/courses/"),)
    service = OPENING_COURSES

    def __init__(self, target: str) -> None:
        super().__init__()
        self.slug = slug_from(target)

    async def _course(self, client: CoursesClient) -> Course:
        try:
            details = (await client.get_product(self.slug))["details"]
        except FetchError as exc:
            if exc.status != 404:
                raise
            raise UnknownTarget(
                f"chess.com has no opening course with the slug {self.slug!r}",
                status=404,
            ) from None
        product = details.get("product") or {}
        name = product.get("name") or ""
        colors = product.get("color") or []
        # No side to name when the course lists both, or none.
        side = _SIDES.get(colors[0]) if len(colors) == 1 else None
        return Course(
            id=details["defaultMoveTrainerId"],
            name=f"{name} ({side})" if name and side else "",
        )
