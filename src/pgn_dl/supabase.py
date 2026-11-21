"""Supabase Auth sessions, for sources whose site signs in through Supabase.

A site built on @supabase/ssr keeps the signed-in session in one cookie,
``sb-<project>-auth-token``, and its server reads the session back out of that
cookie on every request. Sign-in happens once, in a browser; what persists
afterwards is that session - access token, refresh token and all - refreshed
against the project's GoTrue token endpoint on later runs, so downloading
needs no browser at all. A source sends the session back as the same cookie.

Storage, the sign-in loop, and the refresh call's error taxonomy are the
credential store's. This module adds what is Supabase's: the session schema,
the cookie codec and its chunking, the browser probe, and the refresh
choreography (rotation persisted before use, the refreshed session merged over
the stored one, the expiry margin). It sits outside ``core`` because only the
sources whose sites use Supabase need it.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .core.credentials import CredentialStore
from .core.fetch import FetchError

#: Refresh when the access token has less life left than this. It also keeps
#: the site's own server from refreshing: its Supabase client renews a token
#: within 90 seconds of expiry, which would rotate the refresh token into a
#: cookie this tool never receives and spend the one on disk.
_EXPIRY_MARGIN = 300.0


class SupabaseAuth:
    """One source's Supabase session: storage, cookie codec, sign-in, refresh.

    ``name`` is the source's, and drives its profile directory, login hint and
    message wording. ``auth_url`` is the project's GoTrue base (``.../auth/v1``)
    and ``anon_key`` its public API key. ``cookie_name`` is the session cookie,
    and ``site`` the origin whose cookies the sign-in probe reads.
    """

    def __init__(
        self,
        name: str,
        *,
        auth_url: str,
        anon_key: str,
        cookie_name: str,
        site: str,
    ) -> None:
        self.name = name
        self._anon_key = anon_key
        self._cookie_name = cookie_name
        self._token_url = f"{auth_url}/token?grant_type=refresh_token"
        self._site = site
        self._store = CredentialStore(name, validate=self._require_tokens)

    @property
    def login_hint(self) -> str:
        return self._store.login_hint

    def _require_tokens(self, session: object) -> dict[str, Any]:
        """The session as a dict, or a refusal that names the shape, not the value."""
        if not isinstance(session, dict):
            raise FetchError(
                f"{self.name} session is a {type(session).__name__}, not a map"
            )
        for field in ("access_token", "refresh_token"):
            if not isinstance(session.get(field), str) or not session[field]:
                raise FetchError(f"{self.name} session carries no {field}")
        return session

    def credentials_path(self) -> Path:
        return self._store.credentials_path()

    def save(self, session: dict[str, Any]) -> None:
        """Persist the session owner-only, replacing any previous value."""
        self._store.save(session)

    def load(self) -> dict[str, Any]:
        return self._store.load()

    def decode_cookie(self, value: str) -> dict[str, Any]:
        """The session inside one session-cookie value.

        @supabase/ssr writes ``base64-`` + unpadded base64url JSON; older clients
        wrote the JSON plain. The value is tried as sent first and percent-decoded
        second: some cookie stores hand it back URL-encoded, but a plain-JSON
        session could itself contain a literal percent escape that unquoting would
        mangle. Anything else is refused by shape - the value is a credential and
        must not appear in the error.
        """
        for text in (value, unquote(value)):
            if text.startswith("base64-"):
                b64 = text.removeprefix("base64-")
                try:
                    text = base64.urlsafe_b64decode(
                        b64 + "=" * (-len(b64) % 4)
                    ).decode()
                except binascii.Error, UnicodeDecodeError:
                    continue
            try:
                session = json.loads(text)
            except ValueError:
                continue
            if isinstance(session, dict):
                return self._require_tokens(session)
        raise FetchError(
            f"{self.name} {self._cookie_name} cookie is not a readable session"
        )

    @staticmethod
    def encode_cookie(session: dict[str, Any]) -> str:
        """One session as the cookie value the server parses, always unchunked."""
        raw = json.dumps(session, separators=(",", ":")).encode()
        return "base64-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def join_chunks(self, cookies: list[dict]) -> str | None:
        """The session cookie's value from a browser cookie listing, or None.

        Past ~3180 bytes the browser stores the cookie chunked as ``<name>.0``,
        ``<name>.1``, ...; the server reads the unchunked name first, so the
        whole cookie wins when both forms are present. Chunk order is numeric:
        ``.10`` sorts after ``.2``.
        """
        # A browser can hold host- and domain-scoped copies under one name; last
        # wins here, and a stale-but-valid pick self-heals at the first refresh.
        values = {c.get("name"): str(c.get("value") or "") for c in cookies}
        if whole := values.get(self._cookie_name):
            return whole
        prefix = f"{self._cookie_name}."
        chunks = [
            (int(name.removeprefix(prefix)), value)
            for name, value in values.items()
            if name and name.startswith(prefix) and name.removeprefix(prefix).isdigit()
        ]
        if not chunks:
            return None
        return "".join(value for _, value in sorted(chunks))

    async def probe(self, ctx: Any, page: Any) -> dict[str, Any] | None:
        """The signed-in session out of the browser's cookies, or None so far.

        Polling the cookie rather than waiting for a navigation: sites sign in on
        the same page, and the cookie is the one signal that is there whichever
        path the user took.
        """
        value = self.join_chunks(await ctx.cookies(self._site))
        if not value:
            return None
        try:
            return self.decode_cookie(value)
        except FetchError:
            # A cookie that is not yet a session (or never will be) is not worth
            # failing the poll over; sign-in may still be in flight.
            return None

    async def sign_in(self, *, login_url: str, display_name: str | None = None) -> None:
        """Open a browser so the user can sign in, then store the session."""
        await self._store.sign_in(
            login_url=login_url, probe=self.probe, display_name=display_name
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Trade a refresh token for a fresh session."""
        data = await self._store.refresh(
            self._token_url,
            headers={"apikey": self._anon_key, "Content-Type": "application/json"},
            json={"refresh_token": refresh_token},
        )
        if not isinstance(data, dict):
            raise FetchError(
                f"{self.name} token refresh returned a non-object response"
            )
        return data

    async def cookie(self) -> str:
        """A ``Cookie`` header value carrying the session, refreshed if stale.

        Supabase rotates the refresh token on each exchange, so the refreshed
        session is persisted before it is used; losing the rotation would lock
        the user out until they sign in again.
        """
        session = self.load()
        expires_at = session.get("expires_at")
        if (
            not isinstance(expires_at, int | float)
            or expires_at - time.time() < _EXPIRY_MARGIN
        ):
            try:
                refreshed = self._require_tokens(
                    await self.refresh(session["refresh_token"])
                )
            except FetchError as exc:
                raise FetchError(f"{exc}; {self.login_hint}") from None
            if "expires_at" not in refreshed:
                # GoTrue always sends expires_in; expires_at only sometimes.
                refreshed = refreshed | {
                    "expires_at": int(time.time())
                    + int(refreshed.get("expires_in") or 3600)
                }
            # Merged over the stored session, not substituted for it: a response
            # that omits a field the cookie carried (the user object, say) must
            # not strip it from the cookie the server parses next.
            session = session | refreshed
            try:
                self.save(session)
            except FetchError as exc:
                # Supabase has already rotated the refresh token, so the one on
                # disk is spent whatever happens next: the user signs in again
                # either way.
                raise FetchError(f"{exc}; {self.login_hint}") from None
        return f"{self._cookie_name}={self.encode_cookie(session)}"
