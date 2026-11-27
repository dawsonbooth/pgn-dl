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
Download an Explore lesson into PGN files (requires `pgn-dl login aimchess`).

Give the lesson's URL or id. Each lesson is one file with a game per step, in a
directory per topic.

Downloading a lesson opens it on aimchess, which marks it as started in your
progress. On the free plan it also counts toward the daily lesson limit.
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
Download one of aimchess's tactical motifs. No login needed.

Give the motif's URL (aimchess.com/motif/fork). Each motif is one file,
numbered in the site's order, with its definition and an annotated example.
"""

declaration = SourceDeclaration(
    label="aimchess.com",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=ExploreExtractor,
            target_name="target",
            target_help="Lesson URL or id",
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
            target_help="Motif URL (aimchess.com/motif/fork)",
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
