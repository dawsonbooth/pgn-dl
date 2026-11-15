"""Rewriting aimchess's PGN for other readers.

aimchess's games are ChessBase exports whose comments are HTML, rendered as such
on the site: paragraphs, line breaks, numbered lists, links and entities. Line
breaks in the source come from ChessBase wrapping lines at 80 columns, so they
collapse to spaces. ``[%...]`` drawing and evaluation commands are kept.
``%end%``, which marks where an exercise stops asking for moves, is dropped, as
the site's own PGN export drops it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import chess
import chess.pgn

from ...core.fetch import FetchError
from ...core.pgn import header_value, reexport_pgn

#: The PGN ``Site`` header.
SITE = "aimchess.com"

_END_MARKER = "%end%"

_COMMAND = re.compile(r"\[%[^\]]*\]")
_AROUND_COMMA = re.compile(r"\s*,\s*")
_WHITESPACE = re.compile(r"\s+")
_BREAK_EDGES = re.compile(r" *(\n+) *")
_EXTRA_BREAKS = re.compile(r"\n{3,}")
_SPACES = re.compile(r" {2,}")


class _CommentText(HTMLParser):
    """A comment's HTML as plain text. Only tags start new lines."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._numbers: list[int] = []  # the next item number of each open <ol>
        self._hrefs: list[str | None] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(_WHITESPACE.sub(" ", data))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag in ("p", "ol"):
            self.parts.append("\n\n")
            if tag == "ol":
                self._numbers.append(1)
        elif tag == "li":
            if self._numbers:
                self.parts.append(f"\n{self._numbers[-1]}. ")
                self._numbers[-1] += 1
            else:
                self.parts.append("\n- ")
        elif tag == "a":
            self._hrefs.append(dict(attrs).get("href"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "ol"):
            self.parts.append("\n\n")
            if tag == "ol" and self._numbers:
                self._numbers.pop()
        elif tag == "a" and self._hrefs:
            href = self._hrefs.pop()
            # Some link texts don't say which lesson they point to; the
            # address does.
            if href:
                self.parts.append(f" ({urljoin('https://aimchess.com/', href)})")


def _tidy_command(command: str) -> str:
    """A ``[%...]`` command without ChessBase's line wrapping.

    ChessBase wraps inside commands too (``[%cal Gf3f2,\\nGf2e1]``), and strict
    readers such as python-chess and Lichess then drop the drawing.
    """
    inner = _AROUND_COMMA.sub(",", command[2:-1].strip())
    return f"[%{_WHITESPACE.sub(' ', inner)}]"


def comment_text(raw: str) -> str:
    """One comment as plain text, with its ``[%...]`` commands in front.

    Commands come out before the HTML is parsed, as the site does, so a
    paragraph break can't land between a command and the text.
    """
    commands = [_tidy_command(c) for c in _COMMAND.findall(raw)]
    parser = _CommentText()
    parser.feed(_COMMAND.sub(" ", raw).replace(_END_MARKER, " "))
    parser.close()
    text = _BREAK_EDGES.sub(r"\1", "".join(parser.parts))
    text = _SPACES.sub(" ", _EXTRA_BREAKS.sub("\n\n", text)).strip()
    return " ".join(part for part in (*commands, text) if part)


def rewrite(
    pgn: str,
    headers: dict[str, str],
    *,
    subject: str,
    preface: str = "",
    player: str | None = None,
    player_moves_first: bool = False,
) -> str:
    """aimchess PGN re-exported under ``headers``, with plain-text comments.

    ``preface`` goes at the start of the game comment. ``player``, if given,
    is written as White or Black for the side the position is played from:
    the side to move if ``player_moves_first``, otherwise the side that
    replies. The other side becomes "?". Upstream puts placeholders or labels
    in those headers ("Tutorial", a quiz category).
    """

    def transform(game: chess.pgn.Game) -> None:
        nodes: list[chess.pgn.GameNode] = [game]
        while nodes:
            node = nodes.pop()
            node.comment = comment_text(node.comment)
            node.starting_comment = comment_text(node.starting_comment)
            nodes.extend(node.variations)
        if preface:
            parts = (comment_text(preface), game.comment)
            game.comment = "\n\n".join(part for part in parts if part)
        if player is not None:
            to_move = game.board().turn
            side = to_move if player_moves_first else not to_move
            color = "White" if side == chess.WHITE else "Black"
            game.headers["White"] = game.headers["Black"] = "?"
            game.headers[color] = header_value(player)

    return reexport_pgn(
        pgn, headers, subject=subject, error=FetchError, transform=transform
    )
