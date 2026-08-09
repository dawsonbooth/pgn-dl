"""What noctie's two sources share: target parsing and the run's ID token.

Themes and opening books live in their own modules, since one hands over
pre-rendered PGN and the other a move tree, but they address their material the
same way and authenticate the same way.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from ...core.extractor import Extractor
from ...core.fetch import FetchError
from . import auth
from .firestore import guarded_read

#: Where a downloaded game came from, for the PGN ``Site`` header.
SITE = "noctie.ai"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def target_pattern(route: str) -> re.Pattern[str]:
    """Match a noctie URL under ``route``, or a bare id.

    The prefix is optional so a bare id works, but it is all-or-nothing: with
    the host itself optional, https://example.com/<route>/<uuid> would be
    accepted too. noctie's own routes are inconsistent about plurals (themes
    plural, opening books singular) and both forms get pasted, so callers pass a
    route that reads either, like ``themes?``.
    """
    return re.compile(
        rf"^(?:https?://(?:app\.)?noctie\.ai/{route}/)?(?P<id>{_UUID})/?$",
        re.IGNORECASE,
    )


def id_from(target: str, pattern: re.Pattern[str], noun: str) -> str:
    """The document id in a noctie URL, or a bare id.

    Matching is purely by pattern, and deliberately strict: noctie's SPA serves
    HTTP 200 for every path, so a wrong target produces no error of its own --
    it would just read an empty document.
    """
    # Share links carry a ?query and in-page links a #fragment; neither
    # addresses the document, and the pattern is end-anchored.
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = pattern.match(cleaned)
    if m is None:
        raise ValueError(f"could not read a noctie {noun} id from {target!r}")
    return m.group("id").lower()


class NoctieExtractor(Extractor):
    """Base for noctie's sources: no browser, and one ID token at a time.

    Downloading needs no browser: `pgn-dl login noctie` stores a refresh token
    once, and every read is plain HTTPS against the Firestore REST API with a
    minted ID token. Every read goes through :meth:`read`, which is where that
    token's one-hour lifetime is dealt with.
    """

    def __init__(self, target: str) -> None:
        """Build the extractor for one target: a noctie URL, or a bare id.

        Subclasses do the parsing, since the route and the noun differ per
        source. The signature is declared here because :meth:`from_url` builds
        ``cls(url)`` and would not type-check against a constructor taking none.
        """
        # Minted on first use, so constructing an extractor costs nothing.
        self._token = ""
        # How many times it has been minted, so a read that has just been
        # refused can say which token was refused.
        self._minted = 0
        self._token_lock = asyncio.Lock()

    async def _id_token(self, *, refused: int = 0) -> tuple[str, int]:
        """The run's ID token, and the number of mints it is from.

        ``refused`` is that number as seen by a read that has just been answered
        401, so the token is re-minted only while it is still the current one.
        Concurrency is the reason for both the lock and the count: at the hour
        mark every read in flight is refused at once, and at ``-j 4`` that is
        four re-mints of the same refresh token. Google rotates that refresh
        token on each exchange and :func:`auth.id_token` persists whichever
        comes back last, so racing exchanges can leave the stored credential
        holding a superseded one, after which no later run starts at all until
        the user signs in again.
        """
        async with self._token_lock:
            if self._token and self._minted != refused:
                return self._token, self._minted
            self._token = await auth.id_token()
            self._minted += 1
            return self._token, self._minted

    async def read[T](self, make: Callable[[str], Awaitable[T]], noun: str) -> T:
        """Run one Firestore read, re-minting the ID token if it has expired.

        ``make`` is handed a live ID token and returns the read to await, so a
        retry issues a fresh request rather than re-awaiting a spent one.

        An ID token lasts an hour, and a long download outlives it: at that
        moment every remaining item fails at once. Nothing is written for them,
        so a re-run resumes, but the whole tail of the run is wasted. A 401 is
        exactly that expiry, so mint a fresh token and try the read again.

        Once, and once only. A second 401, holding a token seconds old, is a
        real authorisation failure; retrying it would spin instead of reporting
        it.
        """
        token, minted = await self._id_token()
        try:
            return await guarded_read(make(token), noun)
        except FetchError as exc:
            # The typed status, not the message text: the message embeds the
            # document path, and a document id can contain any digits.
            if exc.status != 401:
                raise
        # Outside the handler: if the retry fails too, that failure is the one
        # worth reporting, and the expired-token attempt only lengthens it.
        token, _ = await self._id_token(refused=minted)
        return await guarded_read(make(token), noun)
