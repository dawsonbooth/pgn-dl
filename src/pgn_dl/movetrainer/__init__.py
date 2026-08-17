"""The MoveTrainer format: Chessable's training-content format, which chess.com
courses inherited through the sites' partnership (see CONTEXT.md).

The format's semantics live here: the annotation-block item model, the NAG
glyph vocabulary, glyph attachment, the half-move scale, the anchoring rules,
and the mainline replay walk. Source packages decode their own wire envelopes
into :class:`DecodedPly` and get the move tree back from :func:`build_tree`.

ADR-0001 places this beside ``core`` and ``sources`` rather than inside
either: the commonality here is kinship, not universality, and every
directory under ``sources`` is one registered source.
"""

from .blocks import Block, BlockComment, BlockItem, BlockMove, parse_annotation_data
from .glyphs import glyph_nags, strip_san
from .text import strip_html
from .tree import DecodedPly, MoveTrainerError, UnplayableMoveError, build_tree

__all__ = [
    "Block",
    "BlockComment",
    "BlockItem",
    "BlockMove",
    "DecodedPly",
    "MoveTrainerError",
    "UnplayableMoveError",
    "build_tree",
    "glyph_nags",
    "parse_annotation_data",
    "strip_html",
    "strip_san",
]
