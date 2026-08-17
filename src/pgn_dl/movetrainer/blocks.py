"""The annotation-block item model and the block builder.

An annotation block is a flat run of move-number-tagged SAN and prose embedded
in a MoveTrainer comment (see CONTEXT.md). Both sites encode one as a ``V``
entry whose ``val`` holds ``S`` (one SAN move each) and ``C`` (prose) entries;
the comment carrier wrapped around the entry list differs per site and lives in
the source packages.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from .glyphs import strip_san
from .text import strip_html


class BlockMove(NamedTuple):
    san: str
    nags: list[int]
    raw: str = ""  # as the site wrote it, move number and glyphs included


class BlockComment(NamedTuple):
    text: str


# One entry of an annotation block: a move, or a comment on the move before it.
BlockItem = BlockMove | BlockComment
Block = list[BlockItem]


def block_text(block: Block) -> str:
    """An annotation block as the sentence the site's editor composed.

    Its moves and prose alternate in reading order, move numbers included, so
    joining them recovers the author's line.
    """
    return " ".join(
        item.raw or item.san if isinstance(item, BlockMove) else item.text
        for item in block
    )


def _attach_glyphs(block: Block, raw: str, nags: list[int]) -> None:
    """Put a glyph-only entry's NAGs on the move it annotates.

    An ``S`` entry that strips to no move at all held nothing but an evaluation
    glyph, so it annotates the entry before it. With no move before it there is
    nothing to annotate and the glyph is kept as text, because a glyph that is
    neither a NAG nor prose is an annotation deleted from the lesson.
    """
    for i in reversed(range(len(block))):
        item = block[i]
        if isinstance(item, BlockMove):
            block[i] = item._replace(nags=[*item.nags, *nags])
            return
    block.append(BlockComment(raw))


def parse_annotation_data(
    data: list[dict], *, clean_text: Callable[[str], str] = strip_html
) -> tuple[str, list[Block]]:
    """Split one annotation ``data`` list into ``(text, blocks)``.

    ``text`` is the concatenated prose; each block is a flat list of
    :class:`BlockMove` / :class:`BlockComment` items in reading order.
    ``clean_text`` collapses one prose fragment to plain text, so a source
    with extra markup of its own can wrap :func:`strip_html`.
    """
    text_parts: list[str] = []
    blocks: list[Block] = []
    for entry in data:
        key = entry.get("key")
        val = entry.get("val")
        if key == "C" and isinstance(val, str):
            t = clean_text(val)
            if t:
                text_parts.append(t)
        elif key == "V" and isinstance(val, list):
            block: Block = []
            for v in val:
                vk = v.get("key")
                vv = v.get("val")
                if vk == "S" and isinstance(vv, str):
                    san, nags = strip_san(vv)
                    if san:
                        block.append(BlockMove(san, nags, vv.strip()))
                    elif nags:
                        _attach_glyphs(block, vv.strip(), nags)
                elif vk == "C" and isinstance(vv, str):
                    trail = clean_text(vv)
                    if trail:
                        block.append(BlockComment(trail))
            if block:
                blocks.append(block)
    return "\n\n".join(text_parts), blocks
