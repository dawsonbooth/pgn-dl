"""The MoveTrainer NAG glyph vocabulary and the move-number prefix."""

from __future__ import annotations

import re

# Move-number prefix on annotation-block SAN, e.g. "9.Ne5?!" or "11...g6". One
# dot is White's move, three are Black's; nothing else distinguishes the two.
MOVE_NUM = re.compile(r"^\s*(\d+)(\.+)\s*")

# Trailing annotation glyphs, in both the figurine and the ASCII spellings the
# sites write. A glyph stripped off the SAN and mapped to nothing would be an
# annotation deleted from the lesson, so the patterns below are built from this
# map: nothing else can match.
_NAG_MAP: dict[str, int] = {
    "!": 1,
    "?": 2,
    "!!": 3,
    "??": 4,
    "!?": 5,
    "?!": 6,
    "=": 10,
    "∞": 13,
    "⩲": 14,
    "+/=": 14,
    "⩱": 15,
    "=/+": 15,
    "±": 16,
    "+/-": 16,
    "∓": 17,
    "-/+": 17,
    "+-": 18,
    "-+": 19,
}

# Longest first, so "!?" reads as one glyph rather than two and "+/-" is not cut
# short at "+". A run is any sequence of them: the sites write "!?!" and "=∞".
_NAG_GLYPH = re.compile(
    "|".join(re.escape(g) for g in sorted(_NAG_MAP, key=len, reverse=True))
)
_NAG_SUFFIX = re.compile(rf"((?:{_NAG_GLYPH.pattern})+)\s*$")


def glyph_nags(glyph: str) -> list[int]:
    """The NAGs a glyph run maps to; ``[]`` for anything not wholly recognised."""
    glyph = glyph.strip()
    # ``_NAG_SUFFIX`` requires at least one glyph, so an empty or non-glyph
    # string fails to match and yields no NAGs - no separate empty check needed.
    if _NAG_SUFFIX.fullmatch(glyph) is None:
        return []
    return [_NAG_MAP[g] for g in _NAG_GLYPH.findall(glyph)]


def strip_san(raw: str) -> tuple[str, list[int]]:
    """Strip move number and trailing NAG glyphs; return ``(clean_san, nags)``.

    A glyph the map does not know stays on the SAN, where it fails to parse
    rather than disappearing.
    """
    s = MOVE_NUM.sub("", raw).strip()
    m = _NAG_SUFFIX.search(s)
    if m is None:
        return s, []
    return s[: m.start()].strip(), [_NAG_MAP[g] for g in _NAG_GLYPH.findall(m.group(1))]
