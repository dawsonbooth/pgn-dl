"""chessendings.com as a pgn-dl source.

chessendings is a Next.js app that signs in through Supabase. Pages are read
over plain HTTPS as the data stream the site's own router fetches. Theory,
the challenges and the guides need no account; Strategy needs one, and Pro
content an account with Pro, signed in with `pgn-dl login chessendings`.
"""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .challenges import ChallengesExtractor
from .guides import GuidesExtractor
from .strategy import StrategyExtractor
from .theory import TheoryExtractor

_THEORY_HELP = """
Download Theory lessons into PGN files.

Give the Theory URL (chessendings.com/train) for every lesson, or a lesson URL
or slug for one. Each lesson is one file, in a directory per family, with the
lesson's essay on the ending, the method move by move with the coach's
comments, and the coach's demonstrations. Re-running with the same out dir
resumes.

The free lessons need no account. For the Pro lessons, sign in with
`pgn-dl login chessendings` as an account with Pro.
"""

_STRATEGY_HELP = """
Download Strategy positions into PGN files (requires
`pgn-dl login chessendings`).

Give the Strategy URL (chessendings.com/practice) for every position, or a
position URL or slug for one. Each position is one file, in a directory per
idea, with the coach's plan and the game it came from. The first five
positions of every idea are free; the rest need Pro.
"""

_CHALLENGES_HELP = """
Download the endgame challenges into PGN files. No login needed.

Give the challenges URL for every category, a theme URL for its categories, or
a category URL or slug for one. Each category is one file with a game per
position, easiest first, each with its condition: mate in N, or hold N moves.
"""

_GUIDES_HELP = """
Download the endgame guides into PGN files. No login needed.

Give the guides URL for every guide, or a guide URL or slug for one. Each
guide is one file, in a directory per chapter, with a game for each line its
diagrams step through and the article's text in their comments.
"""

declaration = SourceDeclaration(
    label="chessendings.com",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=TheoryExtractor,
            target_name="target",
            target_help="Theory URL for every lesson, or a lesson URL or slug",
            help=_THEORY_HELP,
        ),
        ExtractorDeclaration(
            extractor=StrategyExtractor,
            target_name="target",
            target_help="Strategy URL for every position, or a position URL or slug",
            help=_STRATEGY_HELP,
        ),
        ExtractorDeclaration(
            extractor=ChallengesExtractor,
            target_name="target",
            target_help="Challenges, theme or category URL, or a category slug",
            help=_CHALLENGES_HELP,
        ),
        ExtractorDeclaration(
            extractor=GuidesExtractor,
            target_name="target",
            target_help="Guides URL for every guide, or a guide URL or slug",
            help=_GUIDES_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = [
    "ChallengesExtractor",
    "GuidesExtractor",
    "StrategyExtractor",
    "TheoryExtractor",
    "declaration",
    "login",
]
