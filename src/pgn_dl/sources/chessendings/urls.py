"""chessendings URLs: which pages a command owns, and what a target names.

Every page may sit under a language prefix (/fr, /pt-br, ...); the content is
read in English whichever one a URL names (see ``client.py``).
"""

from __future__ import annotations

import re

SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
HOST = r"https?://(?:www\.)?chessendings\.com(?:/[a-z]{2}(?:-[a-z]{2})?)?"

GUIDE_HREF = re.compile(rf"/guides/({SLUG})")


def under(*sections: str) -> tuple[re.Pattern[str], ...]:
    """The URL patterns of the pages under ``sections``, for ``url_patterns``."""
    return (re.compile(rf"^{HOST}/(?:{'|'.join(sections)})(?:[/?#]|$)", re.IGNORECASE),)


def slug_from(target: str, *sections: str, noun: str) -> str | None:
    """The slug of the item a target names, or None for a whole section.

    A target is a section's URL, an item's URL under it, or a bare slug.
    """
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    pattern = (
        rf"^(?:{HOST}/(?:{'|'.join(sections)})(?:/(?P<url>{SLUG}))?/?"
        rf"|(?P<bare>{SLUG}))$"
    )
    m = re.match(pattern, cleaned, re.IGNORECASE)
    if m is None:
        raise ValueError(f"could not read a chessendings {noun} from {target!r}")
    found = m.group("url") or m.group("bare")
    return found.lower() if found else None
