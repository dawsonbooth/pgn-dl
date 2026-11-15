"""Content compiled into aimchess's JavaScript.

The quiz puzzles and the tactical motifs aren't served by the API: they are
object literals in the site's public webpack bundles. An app page names the
entry scripts (the home page is a separate site), and the app script holds
webpack's map of the chunks it loads on demand, which are searched when an
entry script doesn't have the content.
"""

from __future__ import annotations

import re

from ...core.fetch import Fetcher, FetchError

ROOT = "https://aimchess.com"

_SCRIPT_SRC = re.compile(r'<script[^>]*\ssrc="(/[^"]+\.js)"')
#: webpack's chunk filename: "aimchess-assets/"+e+"."+({270:"67deaa64"})[e]+".bundle.js"
_CHUNK_FILENAME = re.compile(r'"([^"]*)"\+e\+"\."\+\(\{([^}]*)\}\)\[e\]\+"([^"]*)"')
_PUBLIC_PATH = re.compile(r'\b\w\.p="(/[^"]*)"')

#: A JavaScript string literal in either quote. Its body is in one of two groups.
STRING = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def string_value(match: re.Match[str], group: int) -> str:
    """The value of a ``STRING`` matched at ``group`` and ``group + 1``."""
    body = match.group(group)
    if body is None:
        body = match.group(group + 1)
    return _ESCAPE.sub(_unescape, body)


def _unescape(m: re.Match[str]) -> str:
    code = m.group(1)
    if len(code) > 1:  # \uXXXX or \xXX
        return chr(int(code[1:], 16))
    return _ESCAPES.get(code, code)


async def find_script(fetcher: Fetcher, page: str, marker: str) -> str:
    """The text of the first app script containing ``marker``.

    ``page`` is the path of an app page, whose HTML names the entry scripts.
    """
    html = await fetcher.get_text(ROOT + page, headers={})
    scripts = [
        await fetcher.get_text(ROOT + src, headers={})
        for src in _SCRIPT_SRC.findall(html)
    ]
    for script in scripts:
        if marker in script:
            return script
    for url in _chunk_urls(scripts):
        script = await fetcher.get_text(url, headers={})
        if marker in script:
            return script
    raise FetchError(f"no aimchess script contains {marker!r}; the site has changed")


def _chunk_urls(scripts: list[str]) -> list[str]:
    for script in scripts:
        filename = _CHUNK_FILENAME.search(script)
        public_path = _PUBLIC_PATH.search(script)
        if filename and public_path:
            prefix, hashes, suffix = filename.groups()
            return [
                f"{ROOT}{public_path.group(1)}{prefix}{chunk}.{digest}{suffix}"
                for chunk, digest in re.findall(r'(\d+):"(\w+)"', hashes)
            ]
    raise FetchError("found no chunk map in aimchess's scripts; the site has changed")
