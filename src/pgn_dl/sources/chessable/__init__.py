"""chessable.com as a pgn-dl source.

Chessable is a React app over a PHP/Cloudflare backend. Sign-in is interactive
and happens once in a browser (chess.com OAuth); the JWT it leaves in
localStorage is stored, and every download after that is plain HTTPS carrying
that token, so no browser is opened for downloads.
"""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .courses import CoursesExtractor

_COURSES_HELP = """
Download a Chessable course into PGN files (requires `pgn-dl login chessable`).

One PGN game per variation, laid out Course -> Chapter -> Variation, with the
author's comments, evaluation glyphs, and board drawings preserved. Re-running
with the same out dir resumes, skipping PGNs that already exist.
"""

declaration = SourceDeclaration(
    label="chessable.com",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=CoursesExtractor,
            target_name="course",
            target_help="Course id or chessable.com URL",
            help=_COURSES_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = ["CoursesExtractor", "declaration", "login"]
