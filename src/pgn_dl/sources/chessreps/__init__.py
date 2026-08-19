"""chessreps.com as a pgn-dl source.

chessreps is a SvelteKit app backed by Supabase. Sign-in is interactive and
happens once in a browser; every download after that is one plain-HTTPS read
of the site's own data route, authenticated by the stored session cookie.
"""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .openings import OpeningsExtractor

_OPENINGS_HELP = """
Download an opening course into PGN files (requires `pgn-dl login chessreps`).

The whole repertoire is written as one file, mainline first and every
other line as a variation, with the author's commentary and the site's
engine evals as move comments. Each line is written again as a file of its
own under lines/. Re-running with the same out dir resumes.
"""

declaration = SourceDeclaration(
    label="chessreps.com",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=OpeningsExtractor,
            target_name="opening",
            target_help="Opening URL or chessreps course slug",
            help=_OPENINGS_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = ["OpeningsExtractor", "declaration"]
