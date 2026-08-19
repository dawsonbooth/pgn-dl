"""The credential store: one source's persisted credential (see CONTEXT.md).

Owns the invariants every credential-bearing source shares: validate before
any filesystem side effect, an owner-only profile directory and file, a
whole-or-nothing atomic write, a symlinked credential path refused rather than
followed, and no error message that ever carries the secret. It also owns the
interactive sign-in poll loop and the guarded refresh call's error taxonomy -
exception type name only, status code only, "malformed response" - so that
none of them drift between sources.

A source supplies only what differs: its name (which drives its profile
directory, its login hint, and its message wording), its credential schema,
its browser probe, its login URL, and its refresh protocol. Post-refresh
choreography - rotation persistence, session merging, expiry margins, token
extraction - stays with the source, since that is where its context lives.

chess.com has no credential (see CONTEXT.md) and so is not a
:class:`CredentialStore` adapter, but its login still rides the shared
:func:`sign_in_loop`, which is why the loop is a free function rather than a
store method.

ADR-0001 places this in ``core`` rather than beside a particular source: the
three platforms behind noctie, chessreps, and chessable (Firebase, Supabase,
and a bespoke JWT) are unrelated, so their sharing one credential shape is
intrinsic to "having a login flow" - exactly the universality ``core`` is for,
not the kinship that keeps the MoveTrainer format out of it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import typer

from .fetch import FetchError
from .io import private_dir, write_text_atomic
from .paths import profile_dir as _source_profile_dir

_SIGN_IN_POLLS = 300  # every 3 seconds, so 15 minutes
_POLL_INTERVAL = 3.0

#: Called with (ctx, page) on every poll; returns the found value, or None to
#: keep waiting.
Probe = Callable[[Any, Any], Awaitable[Any | None]]


async def sign_in_loop(
    name: str,
    *,
    login_url: str,
    probe: Probe,
    on_success: Callable[[Any], None],
    display_name: str | None = None,
) -> None:
    """Open a visible browser at ``login_url`` and poll until ``probe`` succeeds.

    ``probe`` runs every three seconds against the browser context and its
    page, and returns whatever it found or ``None`` to keep waiting. The first
    non-``None`` result is handed to ``on_success`` and the loop returns; a
    probe that never succeeds within fifteen minutes ends the loop with the
    standard give-up error instead.

    ``display_name`` names the source in the greeting only, for a source whose
    branding (Chessable, chess.com) is not its lowercase registry name. The
    give-up error and everything else still use ``name``, since those must
    match what ``pgn-dl login <name>`` expects.
    """
    from .browser import browser_context

    typer.echo(
        f"A browser window is open. Sign in to {display_name or name}; "
        "this exits automatically."
    )
    profile = _source_profile_dir(name)
    # Owner-only before the browser writes its own copy of the session into it.
    private_dir(profile)
    async with browser_context(profile, headless=False) as ctx:
        page = await ctx.new_page()
        await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(_SIGN_IN_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            value = await probe(ctx, page)
            if value is not None:
                on_success(value)
                return
    raise FetchError(
        f"gave up waiting for {name} sign-in after 15 minutes; "
        f"nothing was saved. Run: pgn-dl login {name} to try again"
    )


class CredentialStore:
    """One source's persisted credential: storage, sign-in, guarded refresh.

    ``name`` drives the profile directory, the login hint, and message
    wording. ``validate`` is the source's credential schema: given a payload
    about to be saved, or just loaded, it returns the validated dict or raises
    :class:`FetchError` naming the missing or malformed field - never the
    value, since a refusal is seen and the value is the secret.
    """

    def __init__(
        self, name: str, *, validate: Callable[[object], dict[str, Any]]
    ) -> None:
        self.name = name
        self._validate = validate

    @property
    def login_hint(self) -> str:
        return f"run: pgn-dl login {self.name}"

    def profile_dir(self) -> Path:
        """Directory holding the credential file and the source's Chrome profile."""
        return _source_profile_dir(self.name)

    def credentials_path(self) -> Path:
        return self.profile_dir() / "credentials.json"

    def save(self, payload: dict[str, Any]) -> None:
        """Persist ``payload`` owner-only, replacing any previous value.

        Written whole or not at all: a truncated or empty credentials.json
        locks the user out until they sign in again, and two runs rotating a
        credential at once would otherwise blend their writes into one that
        parses and is valid for neither.
        """
        validated = self._validate(payload)  # before any filesystem side effect
        private_dir(self.profile_dir())
        path = self.credentials_path()
        # The rename below would replace a link rather than write through it,
        # but a link here is either an attack or an arrangement we should not
        # silently destroy, so say so instead.
        if path.is_symlink():
            raise FetchError(
                f"{path} is a symbolic link; refusing to write credentials through it"
            )
        try:
            write_text_atomic(path, json.dumps(validated))
        except OSError as exc:
            # The message, never the secret: this reaches the runner and the log.
            reason = exc.strerror or type(exc).__name__
            raise FetchError(
                f"could not save {self.name} credentials to {path}: {reason}"
            ) from None

    def load(self) -> dict[str, Any]:
        """The stored credential, or a refusal ending in the login hint."""
        path = self.credentials_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            raise FetchError(
                f"no saved {self.name} credentials at {path}; {self.login_hint}"
            ) from None
        try:
            return self._validate(data)
        except FetchError as exc:
            raise FetchError(f"{exc}; {self.login_hint}") from None

    async def sign_in(
        self, *, login_url: str, probe: Probe, display_name: str | None = None
    ) -> None:
        """The shared sign-in loop, saving the probed credential on success."""

        def on_success(payload: Any) -> None:
            self.save(payload)
            typer.echo(f"Signed in. Credentials saved to {self.credentials_path()}")

        await sign_in_loop(
            self.name,
            login_url=login_url,
            probe=probe,
            on_success=on_success,
            display_name=display_name,
        )

    async def refresh(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        data: Any = None,
    ) -> Any:
        """POST to ``url`` and return the decoded JSON response.

        Every failure leaves here as a :class:`FetchError`, and none of them
        carries the response or the request: a token endpoint echoes the
        request in some error bodies, and both httpx and the body itself may
        carry the credential that produced them.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url, headers=dict(headers or {}), json=json, data=data
                )
        except httpx.HTTPError as exc:
            raise FetchError(
                f"{self.name} token refresh could not reach the source: {type(exc).__name__}"
            ) from None
        if resp.status_code != 200:
            raise FetchError(f"{self.name} token refresh failed: {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise FetchError(
                f"{self.name} token refresh returned a malformed response"
            ) from None
