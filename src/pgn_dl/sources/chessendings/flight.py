"""Decode a React flight stream: what a Next.js page answers to ``RSC: 1``.

chessendings is a Next.js app, and every page it renders on the server can be
requested as the flight stream its client router navigates with instead of as
HTML. The stream is one row per line, ``<hex id>:<payload>``, and most
payloads are JSON. A ``T`` row is a string too long to inline, sent as
``T<hex byte length>,<text>`` with no line break after it. Rows with other
tags (``I`` for a client module, ``HL`` for a preload hint) resolve to None:
nothing read here comes from one.

Inside the JSON, ``$<id>``, ``$L<id>``, ``$@<id>`` and ``$Q<id>`` refer to
another row, ``$undefined`` is undefined, and ``$$`` escapes a literal dollar;
any other ``$`` string is left as it is. A React element is an array,
``["$", type, key, props]``, and the page's tree hangs off row ``0``. A
reference to a row the stream does not contain, or one that leads back to
itself, is refused.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from typing import Any

from ...core.fetch import FetchError

_TEXT_ROW = re.compile(rb"T([0-9a-f]+),")
_ROW_ID = re.compile(r"[0-9a-f]*")
_REFERENCE = re.compile(r"\$[L@Q]?([0-9a-f]+)")
_JSON_START = tuple(b'[{"-0123456789tfn')


def _rows(data: bytes) -> dict[str, bytes | str]:
    """Each row's payload by id: decoded text for a ``T`` row, raw bytes else.

    A row id seen twice keeps its first payload. Only a stream's rows repeat
    an id, its start and its end, and neither is read.
    """
    rows: dict[str, bytes | str] = {}
    pos = 0
    while pos < len(data):
        colon = data.find(b":", pos)
        if colon < 0:
            raise FetchError("chessendings sent a flight row without an id")
        row_id = data[pos:colon].decode("ascii", "replace")
        if not _ROW_ID.fullmatch(row_id):
            raise FetchError(f"chessendings sent a flight row with id {row_id!r}")
        if (text := _TEXT_ROW.match(data, colon + 1)) is not None:
            end = text.end() + int(text.group(1), 16)
            if end > len(data):
                raise FetchError(f"chessendings flight row {row_id} is cut short")
            rows.setdefault(row_id, data[text.end() : end].decode("utf-8"))
            pos = end
            continue
        newline = data.find(b"\n", colon + 1)
        if newline < 0:
            newline = len(data)
        rows.setdefault(row_id, data[colon + 1 : newline])
        pos = newline + 1
    return rows


def parse(stream: str) -> Any:
    """The page's tree, row ``0``, with every reference resolved.

    A ``T`` row's length counts UTF-8 bytes, so the stream is read as the
    bytes it arrived as.
    """
    rows = _rows(stream.encode("utf-8"))
    memo: dict[str, Any] = {}

    def row(row_id: str, active: frozenset[str]) -> Any:
        if row_id in memo:
            return memo[row_id]
        if row_id in active:
            raise FetchError(f"chessendings flight row {row_id} refers to itself")
        try:
            payload = rows[row_id]
        except KeyError:
            raise FetchError(
                f"chessendings flight stream has no row {row_id}"
            ) from None
        value: Any = None
        if isinstance(payload, str):
            value = payload
        elif payload[:1] and payload[0] in _JSON_START:
            try:
                decoded = json.loads(payload)
            except ValueError:
                raise FetchError(
                    f"chessendings flight row {row_id} is not JSON"
                ) from None
            value = resolve(decoded, active | {row_id})
        memo[row_id] = value
        return value

    def resolve(value: Any, active: frozenset[str]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            if value == "$undefined":
                return None
            if value.startswith("$$"):
                return value[1:]
            if (ref := _REFERENCE.fullmatch(value)) is not None:
                return row(ref.group(1), active)
            return value
        if isinstance(value, list):
            return [resolve(item, active) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item, active) for key, item in value.items()}
        return value

    try:
        return row("0", frozenset())
    except RecursionError:
        # References sidestep json.loads's own depth guard, so a chain of
        # them can nest past the interpreter's limit.
        raise FetchError("chessendings flight stream nests too deeply") from None


def is_element(value: Any) -> bool:
    """Whether ``value`` is a React element: ``["$", type, key, props]``."""
    return (
        isinstance(value, list)
        and len(value) == 4
        and value[0] == "$"
        and isinstance(value[3], dict)
    )


def walk(tree: Any) -> Iterator[Any]:
    """Every list and dict in ``tree``, depth first, in document order."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            yield node
            stack.extend(reversed(node))


def find(tree: Any, match: Callable[[Any], bool]) -> Any | None:
    """The first node in document order that ``match`` accepts, or None."""
    return next((node for node in walk(tree) if match(node)), None)


def props_with(tree: Any, *keys: str) -> dict[str, Any] | None:
    """The first props object carrying every one of ``keys``, or None.

    Found by the keys of the data rather than by the component's name, which
    the site can change without changing the data.
    """
    return find(
        tree, lambda node: isinstance(node, dict) and all(k in node for k in keys)
    )
