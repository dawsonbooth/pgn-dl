"""Chess.com as a pgn-dl source."""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .courses import CoursesExtractor
from .lessons import LessonsExtractor

_COURSES_HELP = """
Download a course into PGN files (requires `pgn-dl login chesscom`).

Re-running with the same out dir resumes, skipping PGNs that already exist.
"""

_LESSONS_HELP = """
Download chess.com lessons (requires `pgn-dl login chesscom`).

Give a lesson-course URL to download every lesson in it, or a single lesson
URL. A lesson course is not a chess.com Course; those are
`pgn-dl chesscom courses`. Re-running with the same out dir resumes.
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
            extractor=LessonsExtractor,
            target_name="target",
            target_help="Lesson-course or lesson URL, or 'lesson-course/lesson'",
            help=_LESSONS_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = ["CoursesExtractor", "LessonsExtractor", "declaration"]
