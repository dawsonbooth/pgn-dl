"""Reading data out of minified JavaScript: the literals Learn's levels are.

Learn's stages are object literals in one of the Learn page's modules, such as

    {key:"bishop",title:i18n.learn.theBishop,...,levels:[{goal:i18n.learn.
    grabAllTheStars,fen:"8/8/8/8/8/5B2/8/8 w - -",apples:"d5 g8",nbMoves:2,
    shapes:[n("f3d5"),n("d5g8")]},...].map(t),complete:...}

This reads that much JavaScript and no more: strings, numbers, ``!0`` and
``!1``, arrays, objects with spreads, names and member chains such as
``i18n.learn.theBishop``, calls, arrow functions with one expression for a
body, and an array's ``.map(...)``. A value under a key the caller has no use
for (a level's ``success`` test, say) is skipped over by its brackets rather
than read, so the code in those values never has to be understood. One the
caller does need that is anything else fails, naming where: guessing at it
would put wrong positions in files a later run treats as done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ...core.fetch import FetchError


class ScriptError(FetchError):
    """The script is not in the shape this reader understands."""


@dataclass(frozen=True)
class Name:
    """A name or member chain, e.g. ``("i18n", "learn", "theBishop")``."""

    path: tuple[str, ...]


@dataclass(frozen=True)
class Call:
    callee: Name
    args: tuple[Any, ...]


@dataclass(frozen=True)
class Spread:
    value: Any  # a Name, or a Call such as ``defaults()``


@dataclass(frozen=True)
class Arrow:
    params: tuple[str, ...]
    body: Any


@dataclass(frozen=True)
class Mapped:
    """``array.map(fn)``."""

    array: list[Any]
    fn: Any  # a Name or an Arrow


@dataclass(frozen=True)
class Skipped:
    """A value this reader was told it need not understand."""

    text: str


@dataclass(frozen=True)
class Obj:
    """An object literal as written: ``(key, value)`` pairs and spreads, in order."""

    entries: tuple[tuple[str, Any] | Spread, ...]

    @property
    def spreads(self) -> list[Spread]:
        return [e for e in self.entries if isinstance(e, Spread)]

    def fields(self) -> dict[str, Any]:
        """The key-value entries, later ones winning as in JavaScript."""
        return {e[0]: e[1] for e in self.entries if not isinstance(e, Spread)}


_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_ARROW_PARAMS = re.compile(r"\(((?:[A-Za-z_$][\w$]*(?:,[A-Za-z_$][\w$]*)*)?)\)=>")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?")
_ESCAPE = re.compile(
    r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL
)
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_OPENERS = {"(": ")", "[": "]", "{": "}"}


def _unescape(m: re.Match[str]) -> str:
    code = m.group(1)
    if code.startswith("u{"):
        return chr(int(code[2:-1], 16))
    if len(code) > 1:  # \uXXXX or \xXX
        return chr(int(code[1:], 16))
    return _ESCAPES.get(code, code)


class Reader:
    """A cursor over ``text``, reading one value at a time."""

    def __init__(self, text: str, pos: int = 0) -> None:
        self.text = text
        self.pos = pos

    def fail(self, what: str) -> ScriptError:
        snippet = self.text[self.pos : self.pos + 40]
        return ScriptError(f"{what} at offset {self.pos} ({snippet!r})")

    def peek(self, token: str) -> bool:
        return self.text.startswith(token, self.pos)

    def take(self, token: str) -> None:
        if not self.peek(token):
            raise self.fail(f"expected {token!r}")
        self.pos += len(token)

    def value(self, keep: frozenset[str] | None = None) -> Any:
        """The value at the cursor. ``keep`` limits which object keys are read."""
        text, pos = self.text, self.pos
        if pos >= len(text):
            raise self.fail("unexpected end of script")
        ch = text[pos]
        if ch in "\"'":
            return self._string()
        if ch == "[":
            items = self._list("]", keep)
            if self.peek(".map("):
                self.take(".map(")
                fn = self.value(keep)
                self.take(")")
                return Mapped(items, fn)
            return items
        if ch == "{":
            return self.entries(keep)
        if text.startswith("!0", pos) or text.startswith("!1", pos):
            self.pos += 2
            return text[pos + 1] == "0"
        if ch == "(":
            return self._parenthesized(keep)
        if m := _NUMBER.match(text, pos):
            self.pos = m.end()
            number = m.group()
            return float(number) if "." in number or "e" in number else int(number)
        if m := _NAME.match(text, pos):
            self.pos = m.end()
            if self.peek("=>"):  # e=>body
                self.take("=>")
                return Arrow((m.group(),), self.value(keep))
            path = [m.group()]
            while self.peek(".") and (n := _NAME.match(text, self.pos + 1)):
                path.append(n.group())
                self.pos = n.end()
            name = Name(tuple(path))
            if self.peek("("):
                return Call(name, tuple(self._list(")", keep)))
            return name
        raise self.fail("unreadable value")

    def entries(self, keep: frozenset[str] | None = None) -> Obj:
        """An object literal. Values under keys outside ``keep`` are skipped."""
        self.take("{")
        found: list[tuple[str, Any] | Spread] = []
        while not self.peek("}"):
            if self.peek("..."):
                self.take("...")
                found.append(Spread(self.value(keep)))
            else:
                key = self._key()
                self.take(":")
                if keep is None or key in keep:
                    found.append((key, self.value(keep)))
                else:
                    found.append((key, self.skip()))
            if not self.peek(","):
                break
            self.take(",")
        self.take("}")
        return Obj(tuple(found))

    def skip(self) -> Skipped:
        """Pass over one value whatever it is, by its brackets and strings."""
        text, start = self.text, self.pos
        closing: list[str] = []
        pos = start
        while pos < len(text):
            ch = text[pos]
            if ch in "\"'`":
                pos = self._string_end(pos)
                continue
            if ch in _OPENERS:
                closing.append(_OPENERS[ch])
            elif ch in ")]}":
                if not closing:
                    break
                if closing.pop() != ch:
                    raise self.fail("unbalanced brackets")
            elif ch == "," and not closing:
                break
            pos += 1
        if closing:
            raise self.fail("unexpected end of script")
        self.pos = pos
        return Skipped(text[start:pos])

    def _key(self) -> str:
        if self.text[self.pos] in "\"'":
            return self._string()
        m = _NAME.match(self.text, self.pos)
        if m is None:
            raise self.fail("unreadable key")
        self.pos = m.end()
        return m.group()

    def _list(self, end: str, keep: frozenset[str] | None) -> list[Any]:
        self.pos += 1  # past the opening bracket
        items = []
        while not self.peek(end):
            items.append(self.value(keep))
            if not self.peek(","):
                break
            self.take(",")
        self.take(end)
        return items

    def _parenthesized(self, keep: frozenset[str] | None) -> Any:
        """An arrow's parameters, or a value in parentheses."""
        m = _ARROW_PARAMS.match(self.text, self.pos)
        if m is not None:
            self.pos = m.end()
            params = tuple(p for p in m.group(1).split(",") if p)
            return Arrow(params, self.value(keep))
        self.take("(")
        inner = self.value(keep)
        self.take(")")
        return inner

    def _string(self) -> str:
        start = self.pos
        end = self._string_end(start)
        self.pos = end
        return _ESCAPE.sub(_unescape, self.text[start + 1 : end - 1])

    def _string_end(self, pos: int) -> int:
        """The offset just past the string literal starting at ``pos``."""
        quote, text = self.text[pos], self.text
        pos += 1
        while pos < len(text):
            ch = text[pos]
            if ch == "\\":
                pos += 2
                continue
            if ch == quote:
                return pos + 1
            if quote == "`" and text.startswith("${", pos):
                inner = Reader(text, pos + 2)
                inner.skip()
                pos = inner.pos
                if not text.startswith("}", pos):
                    raise inner.fail("unclosed template substitution")
            pos += 1
        raise self.fail("unterminated string")
