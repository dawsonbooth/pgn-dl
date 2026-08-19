"""The credential store: storage invariants, the sign-in loop, guarded refresh.

No test launches a browser or touches the network; the browser context and the
sign-in poll are faked. A dummy schema stands in for a source's own - the
invariants under test here do not depend on what a credential looks like.
"""

from __future__ import annotations

import stat
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

import pgn_dl.core.browser as browser_module
from pgn_dl.core import credentials as credentials_module
from pgn_dl.core import io as io_module
from pgn_dl.core.credentials import CredentialStore, sign_in_loop
from pgn_dl.core.fetch import FetchError

# Invented throughout: nothing here is a credential, and none of it is ever
# meant to reach a message.
DUMMY = "DUMMY-SECRET-DO-NOT-LEAK"


def _validate(data: object) -> dict[str, Any]:
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("secret"), str)
        or not data["secret"]
    ):
        raise FetchError("widget credentials carry no secret")
    return {"secret": data["secret"]}


def _store(**kw: Any) -> CredentialStore:
    return CredentialStore("widget", validate=_validate, **kw)


@pytest.fixture(autouse=True)
def _profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGN_DL_PROFILE", str(tmp_path))


def test_missing_credentials_end_in_the_login_hint() -> None:
    with pytest.raises(FetchError) as exc:
        _store().load()
    assert "run: pgn-dl login widget" in str(exc.value)


def test_corrupt_credentials_end_in_the_login_hint() -> None:
    store = _store()
    path = store.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(FetchError) as exc:
        store.load()
    assert "run: pgn-dl login widget" in str(exc.value)


def test_a_credential_failing_schema_validation_ends_in_the_login_hint() -> None:
    store = _store()
    path = store.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"secret": ""}', encoding="utf-8")
    with pytest.raises(FetchError) as exc:
        store.load()
    message = str(exc.value)
    assert "carry no secret" in message
    assert "run: pgn-dl login widget" in message


def test_save_validates_before_any_filesystem_side_effect() -> None:
    store = _store()
    with pytest.raises(FetchError):
        store.save({"secret": ""})
    assert not store.profile_dir().exists()


def test_saved_credentials_round_trip_owner_only() -> None:
    store = _store()
    store.save({"secret": DUMMY})
    assert store.load() == {"secret": DUMMY}
    mode = stat.S_IMODE(store.credentials_path().stat().st_mode)
    assert mode == 0o600, f"credentials must be owner-only, got {mode:o}"


def test_saving_narrows_a_pre_existing_world_readable_file() -> None:
    # os.open's mode applies only on creation, so a file restored from a
    # backup or copied between machines could otherwise keep 0644 and hold a
    # long-lived credential world-readable.
    store = _store()
    path = store.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    store.save({"secret": DUMMY})
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"a pre-existing file must be narrowed, got {mode:o}"


def test_the_profile_directory_is_owner_only() -> None:
    store = _store()
    d = store.profile_dir()
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o755)
    store.save({"secret": DUMMY})
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode == 0o700, f"profile dir must be owner-only, got {mode:o}"


def test_saving_twice_replaces_rather_than_appends() -> None:
    store = _store()
    store.save({"secret": "FIRST"})
    store.save({"secret": "SECOND"})
    assert store.load() == {"secret": "SECOND"}


def test_a_symlinked_credentials_file_is_refused() -> None:
    store = _store()
    victim = store.profile_dir().parent / "victim"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("not a credential", encoding="utf-8")
    victim.chmod(0o644)
    path = store.credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(victim)

    with pytest.raises(FetchError):
        store.save({"secret": DUMMY})

    assert victim.read_text(encoding="utf-8") == "not a credential"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_a_symlinked_profile_directory_is_refused() -> None:
    store = _store()
    outside = store.profile_dir().parent / "outside"
    outside.mkdir(parents=True, mode=0o755)
    d = store.profile_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.symlink_to(outside, target_is_directory=True)

    with pytest.raises(FetchError):
        store.save({"secret": DUMMY})

    assert not (outside / "credentials.json").exists()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755


def test_a_save_that_fails_partway_leaves_the_stored_credential_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A truncated or empty credentials.json locks the user out until they sign
    # in again, so the replacement has to be all or nothing.
    import errno

    store = _store()
    store.save({"secret": "FIRST"})

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(io_module.os, "replace", no_space)

    with pytest.raises(FetchError):
        store.save({"secret": "SECOND"})

    assert store.load() == {"secret": "FIRST"}
    assert not list(store.credentials_path().parent.glob("*.tmp"))


def test_a_save_failure_never_echoes_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(io_module.os, "replace", no_space)

    with pytest.raises(FetchError) as exc:
        store.save({"secret": DUMMY})
    assert DUMMY not in str(exc.value)


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com/login"
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **kwargs: object) -> None:
        self.goto_calls.append(url)
        self.url = url


class FakeCtx:
    def __init__(self) -> None:
        self.page = FakePage()

    async def new_page(self) -> FakePage:
        return self.page


def _fake_browser_context(order: list[str] | None = None):
    @asynccontextmanager
    async def factory(profile: Path, *, headless: bool):
        if order is not None:
            order.append("browser_entered")
        yield FakeCtx()

    return factory


@pytest.fixture(autouse=True)
def _no_real_sleep(fast_sign_in_poll: None) -> None:
    pass


async def test_sign_in_saves_on_the_poll_that_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context())
    store = _store()
    calls = 0

    async def probe(ctx: Any, page: Any) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        if calls < 3:
            return None
        return {"secret": DUMMY}

    await store.sign_in(login_url="https://example.com/login", probe=probe)
    assert store.load() == {"secret": DUMMY}
    assert calls == 3


async def test_the_greeting_uses_the_name_by_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context())
    store = _store()

    async def probe(ctx: Any, page: Any) -> dict[str, Any] | None:
        return {"secret": DUMMY}

    await store.sign_in(login_url="https://example.com/login", probe=probe)
    assert "Sign in to widget;" in capsys.readouterr().out


async def test_the_greeting_can_use_a_display_name_instead(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A source's registry name (which drives its paths and its login hint) can
    # differ from how it is actually branded, e.g. chessable's "Chessable".
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context())
    store = _store()

    async def probe(ctx: Any, page: Any) -> dict[str, Any] | None:
        return {"secret": DUMMY}

    await store.sign_in(
        login_url="https://example.com/login", probe=probe, display_name="Widget"
    )
    assert "Sign in to Widget;" in capsys.readouterr().out


async def test_the_give_up_wording_uses_the_name_even_with_a_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The give-up message must match what `pgn-dl login <name>` expects, so it
    # stays on the registry name even when the greeting uses a display name.
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context())
    store = _store()

    async def probe(ctx: Any, page: Any) -> None:
        return None

    with pytest.raises(FetchError) as exc:
        await store.sign_in(
            login_url="https://example.com/login", probe=probe, display_name="Widget"
        )
    message = str(exc.value)
    assert "waiting for widget sign-in" in message
    assert "pgn-dl login widget" in message


async def test_sign_in_gives_up_after_fifteen_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context())
    store = _store()

    async def probe(ctx: Any, page: Any) -> None:
        return None

    with pytest.raises(FetchError) as exc:
        await store.sign_in(login_url="https://example.com/login", probe=probe)
    message = str(exc.value)
    assert "15 minutes" in message
    assert "nothing was saved" in message
    assert "pgn-dl login widget" in message
    assert not store.credentials_path().exists()


async def test_sign_in_goes_to_the_given_login_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_ctx = FakeCtx()

    @asynccontextmanager
    async def factory(profile: Path, *, headless: bool):
        yield seen_ctx

    monkeypatch.setattr(browser_module, "browser_context", factory)
    store = _store()

    async def probe(ctx: Any, page: Any) -> dict[str, Any] | None:
        return {"secret": DUMMY}

    await store.sign_in(login_url="https://example.com/custom-login", probe=probe)
    assert seen_ctx.page.goto_calls == ["https://example.com/custom-login"]


async def test_the_profile_directory_is_made_private_before_the_browser_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context(order))
    real_private_dir = credentials_module.private_dir

    def tracking_private_dir(
        path: Path, *, echo: Callable[[str], None] = print
    ) -> Path:
        order.append("private_dir")
        return real_private_dir(path, echo=echo)

    monkeypatch.setattr(credentials_module, "private_dir", tracking_private_dir)
    store = _store()

    async def probe(ctx: Any, page: Any) -> dict[str, Any] | None:
        return {"secret": DUMMY}

    await store.sign_in(login_url="https://example.com/login", probe=probe)
    # save() on success narrows the directory again, so more than one
    # private_dir call is fine; what matters is that one happens before the
    # browser ever opens.
    assert order[0] == "private_dir"
    assert "browser_entered" in order
    assert order.index("browser_entered") > order.index("private_dir")


async def test_sign_in_loop_without_a_store_can_decline_to_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What chess.com uses: it has no credential of its own (see CONTEXT.md), so
    # the loop's success callback need not persist anything.
    monkeypatch.setattr(browser_module, "browser_context", _fake_browser_context())
    saved: list[Any] = []

    async def probe(ctx: Any, page: Any) -> bool | None:
        return True

    await sign_in_loop(
        "widget",
        login_url="https://example.com/login",
        probe=probe,
        on_success=saved.append,
    )
    assert saved == [True]


async def test_refresh_returns_the_decoded_json_body(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "example.com"
        assert request.headers["x-api-key"] == "abc"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    store = _store()
    result = await store.refresh(
        "https://example.com/token",
        headers={"x-api-key": "abc"},
        json={"refresh_token": DUMMY},
    )
    assert result == {"ok": True}


async def test_refresh_sends_form_data_when_given(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    store = _store()
    await store.refresh(
        "https://example.com/token",
        data={"grant_type": "refresh_token", "refresh_token": DUMMY},
    )
    assert b"grant_type=refresh_token" in seen[0].content


async def test_a_refresh_connection_failure_names_only_the_exception_type(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    store = _store()
    with pytest.raises(FetchError) as exc:
        await store.refresh("https://example.com/token", json={"refresh_token": DUMMY})
    message = str(exc.value)
    assert "ConnectError" in message
    assert DUMMY not in message


async def test_a_refresh_failure_names_only_the_status(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": "invalid_grant", "echo": request.content.decode()}
        )

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    store = _store()
    with pytest.raises(FetchError) as exc:
        await store.refresh("https://example.com/token", json={"refresh_token": DUMMY})
    message = str(exc.value)
    assert "400" in message
    assert DUMMY not in message
    assert "invalid_grant" not in message


async def test_a_malformed_refresh_response_names_neither_body_nor_secret(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client: Callable[..., Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"<html>proxy could not send {DUMMY}")

    monkeypatch.setattr(httpx, "AsyncClient", mock_httpx_client(handler))
    store = _store()
    with pytest.raises(FetchError) as exc:
        await store.refresh("https://example.com/token", json={"refresh_token": DUMMY})
    message = str(exc.value)
    assert "malformed" in message
    assert DUMMY not in message
