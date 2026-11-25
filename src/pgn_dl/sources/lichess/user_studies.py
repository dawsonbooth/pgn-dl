"""A Lichess user's studies (lichess.org/study/by/<user>).

The API lists a user's studies, most recently updated first: all of them when
the saved token is that user's, and otherwise their public ones
(``/api/study/by/<user>``). Each study is one file, its chapters the file's
games, written as ``study.py`` writes a chapter: the export, with what it
leaves out added. The unit here is the study, as in Practice. Files are named
by the study's name and id and are not numbered, since the order changes with
every edit.
"""

from __future__ import annotations

import json
import re

from ...core.extractor import Extractor
from ...core.fetch import Blocked, Fetcher, FetchError
from ...core.models import Collection, GameItem, ItemRef
from ...core.paths import slugify
from . import auth
from .client import ROOT, Client
from .study import ID, TOKEN_REFUSED, Listed, whole_study

_TARGET = re.compile(
    r"^https?://(?:www\.)?lichess\.org/study/by/(?P<user>[A-Za-z0-9_-]+)"
    r"(?:/[a-z]+)?/?$"
)


def parse_user(target: str) -> str:
    """The user a ``/study/by/<user>`` URL names; its sort order is ignored."""
    cleaned = target.strip().split("#", 1)[0].split("?", 1)[0]
    m = _TARGET.match(cleaned)
    if m is None:
        raise ValueError(f"could not read a lichess user's studies from {target!r}")
    return m.group("user")


async def user_studies(client: Client, user: str) -> list[Listed]:
    """The studies the API lists for ``user``, as id and name."""
    try:
        text = await client.text(f"{ROOT}/api/study/by/{user}", auth=True)
    except Blocked:
        raise
    except FetchError as exc:
        if exc.status == 401:
            raise FetchError(TOKEN_REFUSED, status=401) from None
        raise
    found = []
    # Split on newlines alone: a name may hold a character ``splitlines``
    # would also break at (U+2028, say).
    for line in text.split("\n"):
        if not line.strip():
            continue
        entry = json.loads(line)
        study = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(study, str) or not re.fullmatch(ID, study):
            raise FetchError(f"lichess lists a study by {user} without an id")
        name = entry.get("name")
        found.append(Listed(study, name if isinstance(name, str) else ""))
    return found


class UserStudiesExtractor(Extractor[Client]):
    """Every study the API lists for a user."""

    key = "lichess:user-studies"
    url_patterns = (re.compile(r"^https?://(?:www\.)?lichess\.org/study/by/"),)

    def __init__(self, target: str) -> None:
        super().__init__()
        self.user = parse_user(target)

    async def _discover(
        self, fetcher: Fetcher
    ) -> tuple[Collection, list[ItemRef], Client]:
        client = Client(fetcher, token=auth.saved_token())
        listed = await user_studies(client, self.user)
        if not listed:
            raise FetchError(f"lichess lists no studies by {self.user}")
        collection = self.collection(
            slug=slugify(self.user), name=f"Studies by {self.user}"
        )
        # The id keeps two studies of one name apart, as in a study's own
        # directory name.
        refs = [
            ItemRef(id=s.id, path=(slugify(f"{slugify(s.name, 'study')}-{s.id}"),))
            for s in listed
        ]
        return collection, refs, client

    async def _fetch(self, context: Client, fetcher: Fetcher, ref: ItemRef) -> GameItem:
        return GameItem(pgn=await whole_study(context, ref.id))
