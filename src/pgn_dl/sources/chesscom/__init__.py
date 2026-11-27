"""Chess.com as a pgn-dl source."""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .courses import CoursesExtractor
from .lessons import LessonsExtractor
from .opening_courses import OpeningCoursesExtractor

_COURSES_HELP = """
Download a course into PGN files (requires `pgn-dl login chesscom`).

Each lesson is one file, in a directory per chapter, both numbered in the
course's order. For an opening course (chess.com/openings/courses), use
`pgn-dl chesscom opening-courses`.
"""

_OPENING_COURSES_HELP = """
Download an opening course into PGN files (requires `pgn-dl login chesscom`).

Give a chess.com/openings/courses/<slug> URL, or just the slug. Each lesson is
one file, in a directory per chapter, both numbered in the course's order.
"""

_LESSONS_HELP = """
Download chess.com lessons into PGN files (requires `pgn-dl login chesscom`).

Give a lesson-course URL to download every lesson in it, or a single lesson
URL. Each lesson is one file with a game per question, numbered in the lesson
course's order. A lesson course is not a chess.com Course; those are
`pgn-dl chesscom courses`.
"""

declaration = SourceDeclaration(
    label="chess.com",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=CoursesExtractor,
            target_name="course",
            target_help="Course UUID or chess.com URL",
            help=_COURSES_HELP,
        ),
        ExtractorDeclaration(
            extractor=OpeningCoursesExtractor,
            target_name="course",
            target_help="Opening course slug or chess.com/openings/courses URL",
            help=_OPENING_COURSES_HELP,
        ),
        ExtractorDeclaration(
            extractor=LessonsExtractor,
            target_name="target",
            target_help="Lesson-course or lesson URL, or 'lesson-course/lesson'",
            help=_LESSONS_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = [
    "CoursesExtractor",
    "LessonsExtractor",
    "OpeningCoursesExtractor",
    "declaration",
]
