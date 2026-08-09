"""Decode chessreps's ``__data.json`` route into one course.

SvelteKit serializes each route node's data as one flat array ("devalue"): a
dict maps keys to element indices, a list holds element indices, scalars are
themselves, and negative indices are sentinels for values JSON cannot carry.
The hydrator below follows exactly that and refuses everything else - a tagged
array, a cycle, an index off the end - because a payload this module does not
recognize means the site has changed shape, and a loud failure beats a guessed
tree.

Hydration touches only the session reference and the course fields the source
reads, so an unknown encoding anywhere else in a payload - a later field the
site adds, whatever it stores in - cannot break it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ...core.fetch import FetchError

#: Scalar sentinels, per devalue's wire format. Undefined and hole both read
#: as None: the distinction is JavaScript's, not this data's.
_SENTINELS = {-1: None, -2: None, -3: math.nan, -4: math.inf, -5: -math.inf, -6: -0.0}


def hydrate(index: int, values: list) -> Any:
    """The value at ``index`` in one flat array, indices followed throughout.

    Resolved indices are memoized: devalue deduplicates repeated values into
    shared references by design, so without the memo a diamond of references
    re-resolves each level twice and a few hundred bytes of payload turn into
    exponential work. The memo also means a shared reference hydrates to one
    shared Python object, which read-only callers cannot tell apart from a
    copy. Cycles hit the per-path ``active`` set before the memo, since a node
    is memoized only once it is complete.
    """
    memo: dict[int, Any] = {}

    def resolve(ix: object, active: frozenset[int]) -> Any:
        if not isinstance(ix, int) or isinstance(ix, bool):
            raise FetchError(
                f"chessreps payload reference is {type(ix).__name__}, not an index"
            )
        if ix < 0:
            try:
                return _SENTINELS[ix]
            except KeyError:
                raise FetchError(
                    f"chessreps payload uses unknown sentinel {ix}"
                ) from None
        if ix >= len(values):
            raise FetchError(f"chessreps payload index {ix} is out of range")
        if ix in memo:
            return memo[ix]
        if ix in active:
            raise FetchError("chessreps payload is cyclic")
        value = values[ix]
        if isinstance(value, dict):
            resolved: Any = {k: resolve(v, active | {ix}) for k, v in value.items()}
        elif isinstance(value, list):
            if value and isinstance(value[0], str):
                raise FetchError(
                    f"chessreps payload uses the tagged type {value[0]!r}, "
                    "which this parser does not know"
                )
            resolved = [resolve(v, active | {ix}) for v in value]
        else:
            return value
        memo[ix] = resolved
        return resolved

    try:
        return resolve(index, frozenset())
    except RecursionError:
        # The flat wire format sidesteps json.loads's depth guard, so a chain
        # of references can nest past the interpreter's limit. Untyped, this
        # escapes the runner's FetchError handling as a raw traceback.
        raise FetchError("chessreps payload nests too deeply") from None


@dataclass(frozen=True)
class Course:
    """One opening course, as the extractor consumes it."""

    id: str
    name: str
    description: str
    player_side: str  # "w" | "b"
    lines: tuple[str, ...]  # raw SAN strings, move numbers included
    line_names: dict[str, str]  # keyed by the exact line string
    descriptions: dict[str, str]  # keyed by 6-field FEN
    evals: dict[str, Any]  # keyed by 4-field FEN
    trial: bool


@dataclass(frozen=True)
class ParsedPage:
    course: Course
    signed_in: bool


def _data_nodes(text: str) -> list[list]:
    # A JSONDecodeError escapes on purpose: it reaches the runner's
    # malformed-listing report, which already names proxies and sign-in pages.
    payload = json.loads(text)
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        raise FetchError("chessreps payload carries no nodes array")
    return [
        node["data"]
        for node in nodes
        if isinstance(node, dict)
        and isinstance(node.get("data"), list)
        and node["data"]
        and isinstance(node["data"][0], dict)
    ]


def _str_map(value: object) -> dict[str, str]:
    """A hydrated map's str->str entries; anything else is dropped.

    Lenient per entry, strict per container: one null description upstream
    should not fail a whole course, but a map that is not a map at all should.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FetchError(f"chessreps map field is a {type(value).__name__}")
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _map_node(values: list, ref: object, what: str) -> dict:
    """One hop through a reference that must land on an encoded map.

    A hop, not a hydration: the map's values stay as references, so a caller
    can pick the entries it reads and never resolve the rest.
    """
    if not isinstance(ref, int) or isinstance(ref, bool) or not 0 <= ref < len(values):
        raise FetchError(f"chessreps {what} reference is malformed")
    node = values[ref]
    if not isinstance(node, dict):
        raise FetchError(f"chessreps {what} is a {type(node).__name__}, not a map")
    return node


def _signed_in(values: list, ref: object) -> bool:
    """Whether the session reference points at anything at all.

    One shallow hop on purpose: only presence matters here, and resolving the
    whole session would copy the live tokens into another dict for nothing -
    and would fail the run on any encoding inside the session this parser does
    not know.
    """
    if not isinstance(ref, int) or isinstance(ref, bool):
        return False
    return 0 <= ref < len(values) and values[ref] is not None


def parse_payload(text: str) -> ParsedPage:
    """Read one ``__data.json`` body into the course and the session's presence.

    Nodes are found by content, never by position: the route tree behind the
    indices shifts whenever the site redeploys.
    """
    nodes = _data_nodes(text)

    signed_in = False
    for data in nodes:
        if "session" in data[0]:
            signed_in = _signed_in(data, data[0]["session"])
            break

    for data in nodes:
        if "userCourseData" in data[0]:
            break
    else:
        raise FetchError(
            "chessreps payload carries no userCourseData; "
            "is the target a chessreps opening page?"
        )

    root = data[0]
    ucd = _map_node(data, root["userCourseData"], "userCourseData")
    course = _map_node(data, ucd.get("data"), "course data")

    def field(key: str) -> Any:
        """Hydrate one consumed course field; absent reads as None."""
        return hydrate(course[key], data) if key in course else None

    lines = field("lines")
    if (
        not isinstance(lines, list)
        or not lines
        or not all(isinstance(x, str) and x.strip() for x in lines)
    ):
        raise FetchError("chessreps course carries no usable lines")
    side = field("playerSide")
    if side not in ("w", "b"):
        raise FetchError(f"chessreps course playerSide is {side!r}, not 'w' or 'b'")
    course_id = field("id")
    if not isinstance(course_id, str) or not course_id:
        raise FetchError("chessreps course carries no id")

    evals = field("positionEvals")
    if evals is None:
        evals = {}
    if not isinstance(evals, dict):
        raise FetchError(f"chessreps positionEvals is a {type(evals).__name__}")

    trial = None
    if "isUserCourseTrial" in root:
        trial = hydrate(root["isUserCourseTrial"], data)

    return ParsedPage(
        course=Course(
            id=course_id,
            name=str(field("displayName") or course_id),
            description=str(field("description") or ""),
            player_side=side,
            lines=tuple(lines),
            line_names=_str_map(field("lineNames")),
            descriptions=_str_map(field("descriptions")),
            evals=evals,
            trial=bool(trial),
        ),
        signed_in=signed_in,
    )
