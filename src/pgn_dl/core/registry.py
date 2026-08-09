"""Which extractors exist, and which one owns a given URL or key."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import TYPE_CHECKING

from .extractor import Extractor

if TYPE_CHECKING:
    # Only for the annotation: the registry stores sub-apps, it does not build
    # them, and core has no other reason to reach for typer.
    import typer

_EXTRACTORS: dict[str, type[Extractor]] = {}
_LOGINS: dict[str, Callable[[], Awaitable[None]]] = {}
_APPS: dict[str, typer.Typer] = {}


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


def register_login(source: str, fn: Callable[[], Awaitable[None]]) -> None:
    """Record a source's login flow, refusing a name already taken.

    Refused for the reason :func:`register` refuses a taken key: a copy-pasted
    source keeping the original's name would silently steal its login.
    """
    if source in _LOGINS:
        raise DuplicateSource(
            f"two sources claim the login name {source!r}; "
            "give the new source its own name."
        )
    _LOGINS[source] = fn


def logins() -> Mapping[str, Callable[[], Awaitable[None]]]:
    return _LOGINS


def register_app(source: str, app: typer.Typer) -> None:
    """Record a source's Typer sub-app under the name it is typed as.

    Duplicates are refused for the reason :func:`register` refuses a taken key:
    a copy-pasted source keeping the original's name would take its place in
    ``--help``, and nothing would say so.
    """
    if source in _APPS:
        raise DuplicateSource(
            f"two sources claim the sub-app name {source!r}; "
            "give the new source its own name."
        )
    _APPS[source] = app


def apps() -> Mapping[str, typer.Typer]:
    return _APPS
