"""Cloud Firestore REST reads for noctie's project.

The app itself talks Firestore over the WebChannel streaming protocol, which is
impractical to replay. The REST API returns the same documents over ordinary
HTTPS, so that is what we use.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Sequence
from typing import Any
from urllib.parse import quote

from ...core.fetch import Fetcher, FetchError

PROJECT = "chessnought"
BASE = (
    f"https://firestore.googleapis.com/v1/projects/{PROJECT}"
    "/databases/(default)/documents"
)

#: Documents per listing request. Firestore caps a page at 300 either way.
PAGE_SIZE = 300


async def guarded_read[T](awaitable: Awaitable[T], noun: str) -> T:
    """Run a read, explaining a 403 rather than passing it on raw.

    Themes and books carry a ``requiresTier``, but branching on it is not
    possible: the document that would hold it is itself what comes back 403.
    Raw, that reads as "403 Missing or insufficient permissions" and says
    nothing about tiers. The original message is kept so a genuine auth fault is
    still diagnosable; anything that is not a 403 passes through untouched.
    """
    try:
        return await awaitable
    except FetchError as exc:
        # The typed status, not the message text: the message embeds the URL,
        # and about one noctie UUID in a hundred contains the substring "403".
        if exc.status != 403:
            raise
        raise FetchError(
            f"{exc}; this {noun} may require a paid noctie tier, or may not be "
            "shared with this account. Access is enforced by noctie, not by "
            "this tool.",
            status=exc.status,
        ) from None


def document_id(resource_name: str) -> str:
    """Last path segment of a Firestore resource name."""
    return resource_name.rsplit("/", 1)[-1]


def _value(v: dict[str, Any]) -> Any:
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "nullValue" in v:
        return None
    if "arrayValue" in v:
        return [_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return decode(v["mapValue"].get("fields", {}))
    if "timestampValue" in v:
        return v["timestampValue"]
    return v


def decode(fields: dict[str, Any]) -> dict[str, Any]:
    """Firestore's typed values as plain Python."""
    return {k: _value(v) for k, v in fields.items()}


def _headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}", "accept": "application/json"}


async def get_document(fetcher: Fetcher, path: str, token: str) -> dict[str, Any]:
    """One document's decoded fields. ``path`` is relative to the documents root."""
    raw = await fetcher.get_text(f"{BASE}/{path}", headers=_headers(token))
    return decode(json.loads(raw).get("fields", {}))


async def list_documents(
    fetcher: Fetcher,
    path: str,
    token: str,
    *,
    mask: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Every document of a collection, following pagination to the end.

    Paging is not optional: a request capped at ``PAGE_SIZE`` returns exactly
    that many and hides the rest, which for a move tree means silently dropping
    whole branches.

    Each returned dict carries the decoded fields plus ``_id``.
    """
    url = f"{BASE}/{path}?pageSize={PAGE_SIZE}"
    if mask:
        url += "".join(f"&mask.fieldPaths={quote(f)}" for f in mask)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    token_param = ""
    while True:
        raw = await fetcher.get_text(url + token_param, headers=_headers(token))
        page = json.loads(raw)
        for doc in page.get("documents", []):
            item = decode(doc.get("fields", {}))
            item["_id"] = document_id(doc.get("name", ""))
            out.append(item)
        nxt = page.get("nextPageToken")
        if not nxt:
            return out
        if nxt in seen:
            # A token handed back a second time would have this loop request
            # the same page forever, in a phase with no per-item output to make
            # the stall visible. Fail the item rather than hang, or return a
            # listing with those documents in it twice.
            raise FetchError(f"{path}: the listing repeated a page token")
        seen.add(nxt)
        # Page tokens are base64 and routinely contain "+", which a query
        # string would decode as a space: the next request comes back 400
        # INVALID_ARGUMENT partway through the collection.
        token_param = f"&pageToken={quote(nxt, safe='')}"
