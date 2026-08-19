"""noctie.ai as a pgn-dl source.

noctie is a Firebase app with no API of its own. Sign-in is interactive and
happens once in a browser; every read after that is plain HTTPS against the
Cloud Firestore REST API.
"""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .books import OpeningBooksExtractor
from .themes import ThemesExtractor

_THEMES_HELP = """
Download a theme into PGN files (requires `pgn-dl login noctie`).

One file per element, numbered in the theme's own order. Re-running with
the same out dir resumes, skipping PGNs that already exist.
"""

_OPENING_BOOKS_HELP = """
Download an opening book and its bookmarks (requires `pgn-dl login noctie`).

The whole repertoire is written as one file, mainline first and every
branch as a variation. Each of the book's bookmarks is written again as a
file of its own under bookmarks/, opening with the moves that lead to it.
"""

declaration = SourceDeclaration(
    label="noctie.ai",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=ThemesExtractor,
            target_name="theme",
            target_help="Theme UUID or noctie.ai theme URL",
            help=_THEMES_HELP,
        ),
        ExtractorDeclaration(
            extractor=OpeningBooksExtractor,
            target_name="book",
            target_help="Opening book UUID or noctie.ai opening book URL",
            help=_OPENING_BOOKS_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = ["OpeningBooksExtractor", "ThemesExtractor", "declaration"]
