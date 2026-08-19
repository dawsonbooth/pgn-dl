"""Which extractors exist, and which one owns a given URL or key."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from .declaration import SourceDeclaration
from .extractor import Extractor

_EXTRACTORS: dict[str, type[Extractor]] = {}
_DECLARATIONS: dict[str, SourceDeclaration] = {}


class UnknownSource(RuntimeError):
    """No registered extractor matches the requested key or URL."""


class DuplicateSource(RuntimeError):
    """Two extractors claim one key, so one would shadow the other."""


def register(cls: type[Extractor]) -> None:
    """Record an extractor under its key, rejecting a malformed or taken one.

    The key names the output directory, so a copy-pasted source that kept the
    original's key would silently write over it. Fail at import instead.
    """
    if ":" not in cls.key:
        raise ValueError(
            f"{cls.__name__}.key must be '<source>:<kind>', got {cls.key!r}"
        )
    if (owner := _EXTRACTORS.get(cls.key)) is not None:
        raise DuplicateSource(
            f"{cls.__name__} claims key {cls.key!r}, already registered by "
            f"{owner.__name__}; give the new source its own key."
        )
    _EXTRACTORS[cls.key] = cls


def iter_extractors() -> Iterator[type[Extractor]]:
    yield from sorted(_EXTRACTORS.values(), key=lambda c: c.key)


def resolve_key(key: str) -> type[Extractor]:
    try:
        return _EXTRACTORS[key]
    except KeyError:
        known = ", ".join(sorted(_EXTRACTORS)) or "none"
        raise UnknownSource(f"unknown source {key!r}; known: {known}") from None


def resolve_url(url: str) -> Extractor:
    for cls in iter_extractors():
        if (got := cls.from_url(url)) is not None:
            return got
    known = ", ".join(sorted(_EXTRACTORS)) or "none"
    raise UnknownSource(f"no source handles {url!r}; known: {known}")


def register_declaration(declaration: SourceDeclaration) -> None:
    """Record a source's declaration, refusing a name already taken.

    Refused for the reason :func:`register` refuses a taken key: a copy-pasted
    source keeping the original's name would silently take its place on the
    command line and in ``pgn-dl login``.
    """
    name = declaration.source
    if (owner := _DECLARATIONS.get(name)) is not None:
        raise DuplicateSource(
            f"{declaration.label!r} claims source name {name!r}, already "
            f"registered by {owner.label!r}; give the new source its own name."
        )
    _DECLARATIONS[name] = declaration


def declarations() -> Mapping[str, SourceDeclaration]:
    return _DECLARATIONS


def has_login(source: str) -> bool:
    """Whether ``source`` declares a login flow.

    Used to decide whether a "your session may have expired" hint names a real
    command, without runner.py needing to know a declaration's shape.
    """
    declaration = _DECLARATIONS.get(source)
    return declaration is not None and declaration.login is not None
