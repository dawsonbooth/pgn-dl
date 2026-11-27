"""lichess.org as a pgn-dl source.

Lichess serves its pages only to a browser, so each kind comes from what
Lichess serves programs wherever that covers it: studies and Practice from
the API and from the JSON lichess.org answers for a study and each of its
chapters, Learn from the files the Learn page loads, and the opening pages
from Lichess's opening table and its explorer. The opening descriptions have
neither, and are read from the opening pages through a visible, signed-out
Chrome window.
The explorer of Lichess games, and private studies, need the API token that
`pgn-dl login lichess` saves; everything else needs no account.
"""

from ...core.declaration import ExtractorDeclaration, SourceDeclaration
from ...core.registry import register_declaration
from .auth import login
from .learn import LearnExtractor
from .openings import OpeningsExtractor
from .practice import PracticeExtractor
from .studies import StudiesExtractor

_LEARN_HELP = """
Download a Lichess Learn stage into PGN files. No login needed.

Give the stage's URL (lichess.org/learn#/3). Each stage of Learn (Chess basics)
is one file, in a directory per category, with a game per level: the position,
the goal, the stars as yellow squares, the hint arrows, and the moves where the
level scripts them. Most levels are a lone piece with no king, which many GUIs
will not open; they are written as Lichess has them.
"""

_PRACTICE_HELP = """
Download a Lichess Practice study into PGN files. No login needed.

Give the study's URL or id. Each study is one file, in a directory per
section, with a game per exercise: its goal (mate in 2, draw in 30, ...) in
the Termination header, and the instructions Lichess shows beside the board as
its first comment.
"""

_STUDIES_HELP = """
Download a Lichess study into PGN files.

Give a study URL for every chapter, or a chapter URL for one. Each chapter is
one file, as Lichess exports it: comments, variations and drawings included.
What the export leaves out is added as comments: the chapter's description,
the study's own on its first chapter, and a gamebook's hints and replies to
wrong moves. A study whose owner turned export off is downloaded chapter by
chapter. Public studies need no login. A private study
needs `pgn-dl login lichess` as an account that may see it.
"""

_OPENINGS_HELP = """
Download a Lichess opening page into PGN files (requires
`pgn-dl login lichess`, and Google Chrome).

Give an opening's page or its key (London_System), or the openings URL
(lichess.org/opening) for the openings page itself. Each opening is one file,
in a directory per family: the line with the page's description and its
results in Lichess games, the popular continuations as the moves after it with
their popularity and results, then the example games. The numbers are those
the page shows under its default settings.

Lichess serves the description only to a browser, so a Chrome window opens to
read it, signed out. Lichess allows a signed-out visitor 30 opening pages per
10 minutes. If it refuses a page over that limit, pgn-dl waits the 10 minutes
out and asks again. To stay under the limit while downloading openings one
after another, pause 21 seconds between runs.
"""

declaration = SourceDeclaration(
    label="lichess.org",
    login=login,
    extractors=(
        ExtractorDeclaration(
            extractor=LearnExtractor,
            target_name="url",
            target_help="Stage URL (lichess.org/learn#/3)",
            help=_LEARN_HELP,
        ),
        ExtractorDeclaration(
            extractor=PracticeExtractor,
            target_name="target",
            target_help="Practice study URL or id",
            help=_PRACTICE_HELP,
        ),
        ExtractorDeclaration(
            extractor=StudiesExtractor,
            target_name="target",
            target_help="Study or chapter URL, or a study id",
            help=_STUDIES_HELP,
        ),
        ExtractorDeclaration(
            extractor=OpeningsExtractor,
            target_name="target",
            target_help="Opening page URL or key (London_System), or "
            "https://lichess.org/opening",
            help=_OPENINGS_HELP,
        ),
    ),
)
register_declaration(declaration)

__all__ = [
    "LearnExtractor",
    "OpeningsExtractor",
    "PracticeExtractor",
    "StudiesExtractor",
    "declaration",
    "login",
]
