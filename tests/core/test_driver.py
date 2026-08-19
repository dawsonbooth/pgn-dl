"""Driver wiring: transport choice, where the browser profile comes from, exit codes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import typer

from pgn_dl.core import driver
from pgn_dl.core.extractor import Extractor
from pgn_dl.core.fetch import FetchError
from pgn_dl.core.models import Collection, GameItem, ItemRef
from pgn_dl.core.runner import Options, Report


class _EmptyExtractor(Extractor[None]):
    """Discovers nothing, so ``run`` completes without any transport traffic.

    It defines no ``key``, so subclassing does not register it globally.
    """

    async def _discover(
        self, fetcher: object
    ) -> tuple[Collection, list[ItemRef], None]:
        return Collection(source="fake", kind="things", slug="s", name="S"), [], None

    async def _fetch(self, context: None, fetcher: object, ref: ItemRef) -> GameItem:
        raise AssertionError("nothing to fetch")


class _BrowserExtractor(_EmptyExtractor):
    needs_browser = True

    def __init__(self, profile: Path | None) -> None:
        super().__init__()
        self._profile = profile

    def browser_profile(self) -> Path | None:
        return self._profile


@asynccontextmanager
async def _no_browser(profile: Path) -> AsyncIterator[object]:
    raise AssertionError("the browser must not be launched")
    yield  # pragma: no cover - unreachable, keeps this a generator


def _spy_browser(seen: list[Path]):
    @asynccontextmanager
    async def fake(profile: Path) -> AsyncIterator[object]:
        seen.append(profile)
        yield object()

    return fake


def test_an_extractor_names_no_browser_profile_by_default() -> None:
    assert _EmptyExtractor().browser_profile() is None


def test_a_browser_extractor_supplies_its_own_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The profile comes from the extractor, not from the caller: a second source
    # with its own profile must not be handed chess.com's.
    seen: list[Path] = []
    monkeypatch.setattr(driver, "browser_fetcher", _spy_browser(seen))
    profile = tmp_path / "profiles" / "fake"

    driver.execute(_BrowserExtractor(profile), Options(out=tmp_path / "out"))

    assert seen == [profile]


def test_a_browser_extractor_without_a_profile_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="browser_profile"):
        driver.execute(_BrowserExtractor(None), Options(out=tmp_path / "out"))


def test_a_transport_that_cannot_be_built_is_reported_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # private_dir refuses a symlinked profile directory, and the transport is
    # built out here rather than inside run(), so nothing was catching the
    # FetchError: `pgn-dl login` printed a message for this and a download
    # printed a traceback.
    @asynccontextmanager
    async def refusing(profile: Path) -> AsyncIterator[object]:
        raise FetchError(f"{profile} is a symbolic link; refusing to use it")
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(driver, "browser_fetcher", refusing)

    with pytest.raises(typer.Exit) as exc:
        driver.execute(
            _BrowserExtractor(tmp_path / "profile"), Options(out=tmp_path / "out")
        )

    assert exc.value.exit_code == 2
    out = capsys.readouterr().out
    assert "symbolic link" in out
    assert "Nothing was downloaded" in out


def test_a_plain_extractor_never_launches_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(driver, "browser_fetcher", _no_browser)

    driver.execute(_EmptyExtractor(), Options(out=tmp_path / "out"))


def test_a_stopped_run_exits_with_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(
        extractor: object, fetcher: object, opts: Options, **kw: object
    ) -> Report:
        return Report(total=3, written=1, stopped=True)

    monkeypatch.setattr(driver, "run", fake_run)

    with pytest.raises(typer.Exit) as exc:
        driver.execute(_EmptyExtractor(), Options(out=tmp_path / "out"))

    assert exc.value.exit_code == 2


def test_a_run_that_listed_nothing_does_not_claim_repeated_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Discovery failing is the one way to stop with nothing listed, and run()
    # has already said what happened.
    async def fake_run(
        extractor: object, fetcher: object, opts: Options, **kw: object
    ) -> Report:
        return Report(stopped=True)

    monkeypatch.setattr(driver, "run", fake_run)

    with pytest.raises(typer.Exit) as exc:
        driver.execute(_EmptyExtractor(), Options(out=tmp_path / "out"))

    assert exc.value.exit_code == 2
    out = capsys.readouterr().out
    assert "repeated blocks" not in out
    assert "Done." not in out


def test_a_run_where_every_item_failed_does_not_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(
        extractor: object, fetcher: object, opts: Options, **kw: object
    ) -> Report:
        return Report(total=3, failed=3)

    monkeypatch.setattr(driver, "run", fake_run)

    with pytest.raises(typer.Exit) as exc:
        driver.execute(_EmptyExtractor(), Options(out=tmp_path / "out"))

    assert exc.value.exit_code == 1


def test_a_partly_failed_run_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One bad item out of many is tallied and the run carries on; the files that
    # did download are the point of the command.
    async def fake_run(
        extractor: object, fetcher: object, opts: Options, **kw: object
    ) -> Report:
        return Report(total=3, written=2, failed=1)

    monkeypatch.setattr(driver, "run", fake_run)

    driver.execute(_EmptyExtractor(), Options(out=tmp_path / "out"))
