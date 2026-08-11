# pgn-dl

Download chess courses you own as annotated PGN files, for offline study or
import into any chess GUI.

Supported sources:

- Chessable courses
- chess.com courses and lessons
- chessreps.com opening courses
- noctie.ai themes and opening books

pgn-dl only downloads content your account already has access to. It does not
bypass paywalls.

## Install

Requires Python 3.14+, [uv](https://docs.astral.sh/uv/), and Google Chrome.

```sh
uv sync --extra browser
uv run playwright install chrome   # skip if Chrome is already installed
```

## Usage

Sign in once per source:

```sh
uv run pgn-dl login chesscom
```

A browser window opens; sign in on the site and the session is saved. Sessions
and credentials live under `.profile/` in the current directory, so run `login`
and downloads from the same place, or set `PGN_DL_PROFILE` to a fixed path. If
a session expires, run `login` again.

Then download by URL:

```sh
uv run pgn-dl https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e
```

or with the source subcommand, passing a URL or a bare id:

```sh
uv run pgn-dl chesscom courses 6445752b-f404-4d8f-9397-4fb03f690d8e
uv run pgn-dl chessable courses 25098
uv run pgn-dl chessreps openings alapin-sicilian-repertoire-ilg8hms
```

`pgn-dl sources` lists every source and the URL patterns it accepts.

Options:

| Option                | Default  | Description                       |
| --------------------- | -------- | --------------------------------- |
| `--out PATH`          | `output` | Directory to write PGN files into |
| `-j, --concurrency N` | `2`      | Items fetched in parallel         |
| `--delay SECONDS`     | `0.4`    | Jittered pause between requests   |
| `--limit N`           | none     | Only fetch the first N items      |

Only chess.com needs a browser for downloads (it sits behind Cloudflare). The
other sources use one just for `login` and download over plain HTTPS.

## Output

Files are organized by source, kind, and collection:

```
output/
└── chesscom/
    └── courses/
        └── <course-slug>/
            ├── all.pgn                # everything, concatenated in order
            ├── 01-<chapter-slug>/
            │   ├── 001-<lesson-slug>.pgn
            │   └── 002-<lesson-slug>.pgn
            └── 02-<chapter-slug>/
```

Author annotations are preserved where PGN can express them: commentary as
move comments, sidelines as variations, evaluation glyphs as NAGs, and board
arrows and highlights as `[%cal]` / `[%csl]` commands.

Re-running a command skips files that already exist, so an interrupted
download picks up where it left off.

## Adding a source

Subclass `Extractor` (see `src/pgn_dl/core/extractor.py`) and implement
`discover()` and `fetch()`. `src/pgn_dl/sources/chesscom/` is the worked
example. Sources talk to a `Fetcher` rather than HTTP directly, so the same
extractor works over plain HTTP or through a browser.

## Development

```sh
uv run pytest
uvx ruff check
uvx ty check
```

## License

Public domain, see [LICENSE](LICENSE).
