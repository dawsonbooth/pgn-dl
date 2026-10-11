"""Anchor annotation blocks and replay the mainline into the core move tree.

The judgement history behind the anchoring rules lives in docs/open-issues.md
sections 5.1 and 5.7; read it before changing anything here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

import chess

from ..core.models import Arrow, Highlight, MoveNode
from .blocks import Block, BlockComment, BlockMove, block_text
from .glyphs import MOVE_NUM


class MoveTrainerError(Exception):
    """A MoveTrainer transform could not faithfully represent its input."""


class UnplayableMoveError(MoveTrainerError):
    """A mainline move that will not play from the position before it.

    Truncating instead would keep a complete-looking short game that the
    runner writes, counts, and skips on every later run, so the walk raises;
    each envelope adapter translates this into its source's own error type.
    """

    def __init__(self, san: str, reason: str) -> None:
        super().__init__(f"unplayable move {san!r}: {reason}")
        self.san = san
        self.reason = reason


def _ply_number(raw: str) -> int | None:
    """The half-move ``raw``'s move number names, or ``None`` if it names none.

    Counted so that White's and Black's are one scale, and the same scale as
    :func:`_board_ply`: comparing the two is what places a block.
    """
    m = MOVE_NUM.match(raw)
    if m is None:
        return None
    return 2 * int(m.group(1)) + (1 if len(m.group(2)) > 1 else 0)


def _board_ply(board: chess.Board) -> int:
    """The half-move ``board`` is waiting for, on :func:`_ply_number`'s scale."""
    return 2 * board.fullmove_number + (0 if board.turn == chess.WHITE else 1)


def _first_ply(block: Block) -> int | None:
    """The half-move the block's first move names, if it names one."""
    first = next((i for i in block if isinstance(i, BlockMove)), None)
    return _ply_number(first.raw) if first is not None else None


class _LineStep(NamedTuple):
    board: chess.Board  # the position its move was played from
    node: MoveNode


def _build_variation_lines(
    anchor_board: chess.Board,
    block: Block,
) -> tuple[list[MoveNode], str]:
    """Turn a flat annotation block into lines playable from ``anchor_board``.

    Returns ``(heads, "")``, the head MoveNode of every line the block opens at
    ``anchor_board``, or ``([], text)`` when the block is not something that can
    be played from there.

    One block is not always one line. The sites' editors pack whatever the
    author typed into it, including several lines from one position, and nothing
    in the encoding separates them; the move number written into each SAN does.
    A number naming a position the line has already left starts a new line
    branching there, which is a sibling of the move that was played from it.

    A block that still will not replay is not corrupt, it is the lesson's
    teaching in a shape this cannot hold, so it comes back as text for the caller
    to keep on the move it annotates. All of it or none: a partial chain would be
    a line the author never wrote.
    """
    heads: list[MoveNode] = []
    # The line being built, so a move number pointing back into it says where
    # to branch.
    line: list[_LineStep] = []
    board = anchor_board.copy()
    pending = ""  # prose waiting for the move it trails
    lead = ""  # prose before the block's first move, which trails nothing

    for item in block:
        if isinstance(item, BlockComment):
            # Several comments on one move accumulate rather than clobber.
            pending = f"{pending}\n\n{item.text}" if pending else item.text
            continue

        # A trailing comment belongs to the move it followed, so it is flushed
        # before the line moves on.
        if pending:
            if line:
                line[-1].node.post_comment = pending
            else:
                lead = pending
            pending = ""

        ply = _ply_number(item.raw)
        if ply is not None and ply != _board_ply(board):
            at = next(
                (i for i, step in enumerate(line) if _board_ply(step.board) == ply),
                None,
            )
            if at is None:
                return [], block_text(block)
            board = line[at].board.copy()
            del line[at:]

        try:
            move = board.parse_san(item.san)
        except ValueError:
            return [], block_text(block)
        node = MoveNode(
            san=item.san,  # keep the site's original spelling of the move
            nags=list(item.nags),
        )
        if line:
            line[-1].node.children.append(node)
        else:
            node.pre_comment = lead
            lead = ""
            heads.append(node)
        line.append(_LineStep(board.copy(), node))
        board.push(move)

    if not heads:
        return [], block_text(block)  # prose, or nothing, with no move to carry it
    if pending:
        line[-1].node.post_comment = pending
    return heads, ""


class DecodedPly(NamedTuple):
    """One mainline ply, decoded from a source's wire envelope.

    ``san`` is the move as the site spelled it (``"--"`` for a null move);
    the prose and annotation blocks are decoded from the comments around the
    move; ``nags``, ``arrows`` and ``highlights`` are the position's own
    annotations.
    """

    san: str
    pre_comment: str = ""
    post_comment: str = ""
    pre_blocks: Sequence[Block] = ()
    post_blocks: Sequence[Block] = ()
    nags: Sequence[int] = ()
    arrows: Sequence[Arrow] = ()
    highlights: Sequence[Highlight] = ()


def build_tree(board: chess.Board, plies: Sequence[DecodedPly]) -> MoveNode:
    """Replay the mainline ``plies`` from ``board``; return the tree's root.

    Every annotation block is anchored by the half-move its own SAN names, a
    block that will not replay comes back as prose on the ply it annotates,
    and an unplayable mainline move raises :class:`UnplayableMoveError`. A null
    move (``"--"``) is played like any other ply: it passes the turn, and the PGN
    writes it as ``--``.
    """
    root = MoveNode(san=None)

    board = board.copy()
    parent_node = root
    parent_board = board.copy()
    # Lines continuing the previous ply. They are siblings of the ply that comes
    # next, and have to be attached after it: children[0] is the mainline, and a
    # sideline there would rewrite the game.
    continuations: list[MoveNode] = []

    for ply in plies:
        try:
            move = board.parse_san(ply.san)
        except ValueError as exc:
            raise UnplayableMoveError(ply.san, str(exc)) from exc

        board.push(move)
        node = MoveNode(
            san=ply.san,
            pre_comment=ply.pre_comment,
            post_comment=ply.post_comment,
            nags=list(ply.nags),
            arrows=list(ply.arrows),
            highlights=list(ply.highlights),
        )
        parent_node.children.append(node)
        parent_node.children.extend(continuations)
        continuations = []

        # The sites hang a block off the ply it annotates without saying whether
        # it replaces that ply or continues it, and one captured lesson holds
        # both. The move number the block writes into its own SAN is what tells
        # them apart.
        for block in [*ply.pre_blocks, *ply.post_blocks]:
            if _first_ply(block) == _board_ply(board):
                # A reply to this ply, so it branches below it.
                heads, unplayable = _build_variation_lines(board, block)
                continuations.extend(heads)
            else:
                # An alternative to this ply, so it branches where the ply did.
                # A block carrying no move numbers at all lands here too: that
                # is what the encoding meant before the sites began writing
                # them. So does one whose numbers name neither position, which
                # will not replay from here either and comes back as text -
                # anchoring it anywhere would be a guess, and a wrong guess
                # replays as moves the author never wrote.
                heads, unplayable = _build_variation_lines(parent_board, block)
                parent_node.children.extend(heads)
            if unplayable:
                node.post_comment = "\n\n".join(
                    part for part in (node.post_comment, unplayable) if part
                )

        parent_board = board.copy()
        parent_node = node

    # The last ply has no mainline move after it, so its continuations are all
    # it has: the first reads as the line's continuation and the rest as
    # variations of that, which is the only shape PGN can hold them in.
    parent_node.children.extend(continuations)

    return root
