"""Firestore REST helpers: value decoding, field masks, and paging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pgn_dl.core.fetch import FetchError
from pgn_dl.sources.noctie.firestore import (
    BASE,
    decode,
    get_document,
    guarded_read,
    list_documents,
)

FIX = Path(__file__).parents[2] / "fixtures" / "noctie"


class FakeFetcher:
    """Serves canned pages and records the URLs asked for."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.gets: list[str] = []

    async def post_json(self, url, *, headers, json):  # pragma: no cover
        raise AssertionError("firestore reads must be GETs")

    async def get_text(self, url, *, headers):
        self.gets.append(url)
        assert headers.get("authorization") == "Bearer tok", "must send the ID token"
        try:
            return self.pages[url]
        except KeyError:
            raise FetchError(f"{url} failed: 404") from None


def test_decode_turns_typed_values_into_plain_python() -> None:
    assert decode(
        {
            "name": {"stringValue": "Pawn Breaks"},
            "index": {"integerValue": "26"},
            "ok": {"booleanValue": True},
            "alt": {"arrayValue": {}},
            "tags": {
                "arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}
            },
        }
    ) == {"name": "Pawn Breaks", "index": 26, "ok": True, "alt": [], "tags": ["a", "b"]}


async def test_get_document_decodes_fields() -> None:
    url = f"{BASE}/themes/T1"
    f = FakeFetcher({url: (FIX / "theme_doc.json").read_text()})
    doc = await get_document(f, "themes/T1", "tok")
    assert doc["name"] == "Beginner’s Guide to 1. e4"
    assert doc["description"] == "by FM Hugo Wernberg"


async def test_list_documents_follows_next_page_token() -> None:
    base = f"{BASE}/themes/T1/elements?pageSize=300"
    page1 = json.dumps(
        {
            "documents": [{"name": "a/1", "fields": {"index": {"integerValue": "1"}}}],
            "nextPageToken": "TOK2",
        }
    )
    page2 = json.dumps(
        {"documents": [{"name": "a/2", "fields": {"index": {"integerValue": "2"}}}]}
    )
    f = FakeFetcher({base: page1, f"{base}&pageToken=TOK2": page2})
    docs = await list_documents(f, "themes/T1/elements", "tok")
    assert [d["index"] for d in docs] == [1, 2]
    assert len(f.gets) == 2  # genuinely paged, not one call


async def test_page_tokens_are_url_encoded() -> None:
    # Firestore page tokens are base64: a raw "+" would decode as a space and
    # the next page would come back 400 INVALID_ARGUMENT, mid-collection.
    base = f"{BASE}/themes/T1/elements?pageSize=300"
    encoded = f"{base}&pageToken=a%2Bb%2Fc%3D%3D"
    page1 = json.dumps({"documents": [], "nextPageToken": "a+b/c=="})
    f = FakeFetcher({base: page1, encoded: json.dumps({"documents": []})})
    await list_documents(f, "themes/T1/elements", "tok")
    assert f.gets == [base, encoded]


async def test_a_repeated_page_token_stops_the_loop() -> None:
    # A server handing back the token it was just given would have the paging
    # loop request the same page forever, in a run with no item-level progress
    # to make the stall visible.
    base = f"{BASE}/themes/T1/elements?pageSize=300"
    page = json.dumps({"documents": [], "nextPageToken": "SAME"})
    f = FakeFetcher({base: page, f"{base}&pageToken=SAME": page})
    with pytest.raises(FetchError) as exc:
        await list_documents(f, "themes/T1/elements", "tok")
    assert "themes/T1/elements" in str(exc.value)


async def test_list_documents_sends_a_field_mask() -> None:
    url = f"{BASE}/themes/T1/elements?pageSize=300&mask.fieldPaths=index&mask.fieldPaths=name"
    f = FakeFetcher({url: json.dumps({"documents": []})})
    await list_documents(f, "themes/T1/elements", "tok", mask=["index", "name"])
    assert f.gets == [url]


async def _raising(exc: Exception) -> None:
    raise exc


async def test_a_denied_read_explains_who_is_refusing_it() -> None:
    # "403 Missing or insufficient permissions" on its own reads as a broken
    # tool. The original message stays so a genuine auth fault is diagnosable.
    denied = FetchError(
        f"{BASE}/openingBooks/B1 failed: 403 Missing or insufficient permissions.",
        status=403,
    )
    with pytest.raises(FetchError) as exc:
        await guarded_read(_raising(denied), "opening book")

    message = str(exc.value)
    assert "Missing or insufficient permissions" in message
    assert "paid noctie tier" in message
    assert "opening book" in message
    assert exc.value.status == 403


async def test_any_other_failure_passes_through_untouched() -> None:
    # An expired token is not a paywall, and telling the user to buy a tier
    # would send them the wrong way.
    expired = FetchError("failed: 401 Missing or invalid token", status=401)
    with pytest.raises(FetchError) as exc:
        await guarded_read(_raising(expired), "theme")

    assert exc.value is expired
    assert "tier" not in str(exc.value)


async def test_the_status_is_read_from_the_type_not_from_the_message() -> None:
    # About one noctie UUID in a hundred contains "403", and the message embeds
    # the document URL; matching on the text would call a healthy read denied.
    not_denied = FetchError(
        f"{BASE}/themes/40340e-3ba1-403f-9a2f-1 failed: 500 upstream error",
        status=500,
    )
    with pytest.raises(FetchError) as exc:
        await guarded_read(_raising(not_denied), "theme")

    assert "tier" not in str(exc.value)


async def test_a_read_that_succeeds_is_returned_as_it_is() -> None:
    async def ok() -> str:
        return "the document"

    assert await guarded_read(ok(), "theme") == "the document"


def test_document_id_comes_from_the_resource_name() -> None:
    from pgn_dl.sources.noctie.firestore import document_id

    assert (
        document_id("projects/p/databases/(default)/documents/themes/abc123")
        == "abc123"
    )
