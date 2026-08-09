"""Shared fakes for the chessreps source tests.

``flatten`` is a miniature encoder for SvelteKit's flattened ("devalue") wire
format, so tests can state payloads as plain Python and stay readable.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.chessreps import auth


def flatten(value: object) -> list:
    """Encode ``value`` as one devalue flat array (index 0 is the root)."""
    out: list = []

    def add(v: object) -> int:
        i = len(out)
        out.append(None)  # reserve the slot so children index after it
        if isinstance(v, dict):
            out[i] = {k: add(x) for k, x in v.items()}
        elif isinstance(v, list):
            out[i] = [add(x) for x in v]
        else:
            out[i] = v
        return i

    add(value)
    return out


def data_json(layout: dict | None, page: dict) -> str:
    """One ``__data.json`` body: a layout node, a null filler, the page node."""
    nodes: list = [None]
    if layout is not None:
        nodes.append({"type": "data", "data": flatten(layout)})
    nodes.append(None)
    nodes.append({"type": "data", "data": flatten(page)})
    return json.dumps({"type": "data", "nodes": nodes})


def course_page(**overrides: Any) -> dict:
    """A minimal authenticated page node, overridable per test."""
    data: dict[str, Any] = {
        "id": "abc1234",
        "lines": ["1. e4 c5 2. c3 Nc6", "1. e4 c5 2. c3 d5"],
        "lineCount": 2,
        "lineNames": {"1. e4 c5 2. c3 Nc6": "Old Main Line"},
        "playerSide": "w",
        "description": "A course blurb.",
        "displayName": "Test Repertoire",
        "descriptions": {},
        "positionEvals": {},
    }
    page: dict[str, Any] = {
        "userCourseData": {"data": data, "id": "abc1234"},
        "isUserCourseTrial": False,
    }
    for key, value in overrides.items():
        (data if key in data else page)[key] = value
    return page


def signed_in_layout() -> dict:
    return {"session": {"access_token": "DUMMY", "user": {"id": "u"}}, "user": None}


def signed_out_layout() -> dict:
    return {"session": None, "user": None}


class FakeFetcher:
    """Serves one payload per URL, asserting the session cookie is sent."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.gets: list[str] = []

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("chessreps reads must be GETs")

    async def get_text(self, url, *, headers):
        self.gets.append(url)
        assert headers.get("Cookie") == "sb-api-auth-token=STUB", (
            "must send the session cookie"
        )
        try:
            return self.pages[url]
        except KeyError:
            raise FetchError(f"{url} failed: 404", status=404) from None


@pytest.fixture
def stub_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read the credential file or call Supabase."""

    async def fake_cookie() -> str:
        return "sb-api-auth-token=STUB"

    monkeypatch.setattr(auth, "session_cookie", fake_cookie)
