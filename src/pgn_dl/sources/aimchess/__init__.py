"""aimchess.com as a pgn-dl source.

Explore lessons and Checkmate Patterns come from aimchess's REST API with the
JWT that `pgn-dl login aimchess` stores. The quiz and the tactical motifs are
compiled into the site's JavaScript and need no login.
"""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .checkmates import CheckmatePatternsExtractor
from .explore import ExploreExtractor
from .motifs import MotifsExtractor
from .quiz import QuizExtractor

_EXPLORE_HELP = """
Download Explore lessons into PGN files (requires `pgn-dl login aimchess`).

Give the Explore URL to download every lesson, or a lesson URL or id for one.
Each lesson is one file with a game per step, in a directory per topic.
Re-running with the same out dir resumes.

Downloading a lesson opens it on aimchess, which marks it as started in your
progress. On the free plan it also counts toward the daily lesson limit; once
aimchess refuses a lesson, the rest are left for a later run.
"""

_CHECKMATE_PATTERNS_HELP = """
Download the Checkmate Patterns module (requires `pgn-dl login aimchess`).

One file per pattern, each a position, the opponent's move and the mating
line.
"""

_QUIZ_HELP = """
Download the aimchess quiz's 100 puzzles. No login needed.

One file per category (opening, middlegame, endgame, strategy, tactics), 20
puzzles each, easiest first, with the best move and a wrong one explained.
"""

_MOTIFS_HELP = """
Download aimchess's 20 tactical motifs. No login needed.

Any motif URL downloads all of them, one file per motif with its definition
and an annotated example.
"""

declaration = SourceDeclaration(
    label="aimchess.com",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=ExploreExtractor,
            target_name="target",
            target_help="Explore URL for every lesson, or a lesson URL or id",
            help=_EXPLORE_HELP,
        ),
        ExtractorDeclaration(
            extractor=CheckmatePatternsExtractor,
            target_name="url",
            target_help="https://aimchess.com/modules/checkmate_flashcards",
            help=_CHECKMATE_PATTERNS_HELP,
        ),
        ExtractorDeclaration(
            extractor=QuizExtractor,
            target_name="url",
            target_help="https://aimchess.com/public/quiz",
            help=_QUIZ_HELP,
        ),
        ExtractorDeclaration(
            extractor=MotifsExtractor,
            target_name="url",
            target_help="Any motif URL, e.g. https://aimchess.com/motif/fork",
            help=_MOTIFS_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = [
    "CheckmatePatternsExtractor",
    "ExploreExtractor",
    "MotifsExtractor",
    "QuizExtractor",
    "declaration",
    "login",
]
