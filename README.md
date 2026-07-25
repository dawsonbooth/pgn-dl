# chess-course-to-pgn

Export chess.com courses you own into annotated PGN files you can study offline,
import into a database, or open in any chess GUI.

chess.com serves course content through an internal RPC API rather than plain
PGN. This tool signs in with your own account, walks a course's chapters and
lessons, and rebuilds each lesson's move tree into standard PGN, preserving
mainlines, sub-variations, comments, evaluation glyphs (NAGs), and board
drawings.

> **Use it only for courses your own account has access to.** It automates the
> same requests your browser makes when you study a course; it does not bypass
> paywalls or share content.

## How it works

1. **`login`** opens a real Google Chrome window (via Playwright) so you can
   sign in to chess.com once. The session is stored in a local `.profile/`
   directory and reused by later runs.
2. **`scrape`** launches Chrome headless, exchanges your session cookie for a
   bearer token, fetches the course outline, and then requests each lesson.
   Every lesson is parsed and written to disk immediately, so an interrupted
   run loses at most the in-flight requests.

Chess.com sits behind Cloudflare, which is why the tool drives an actual Chrome
profile instead of raw HTTP: the browser's cookies and TLS fingerprint are what
get the API requests accepted.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Google Chrome (installed on your machine, or via `playwright install chrome`)

## Setup

```sh
uv sync
uv run playwright install chrome   # skip if Chrome is already installed
```

## Usage

### 1. Sign in (once)

```sh
uv run chess-course-to-pgn login
```

A browser window opens. Log in to chess.com, then press Enter in the terminal
to save the session. You only need to repeat this if the session expires.

The session lives in a `.profile/` directory resolved from the current working
directory, so run `login` and `scrape` from the same place. To keep a session
that works from anywhere, point both at a fixed location:

```sh
export CHESS_COURSE_PROFILE=~/.chess-course-profile
```

### 2. Scrape a course

```sh
uv run chess-course-to-pgn scrape <course>
```

`<course>` is the course's UUID or any chess.com URL that contains it. The
easiest source is a course's training URL, which has the form
`https://www.chess.com/courses/train/<uuid>`. Pass either the whole URL or just
the UUID:

```sh
uv run chess-course-to-pgn scrape https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e
uv run chess-course-to-pgn scrape 6445752b-f404-4d8f-9397-4fb03f690d8e
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--out PATH` | `output` | Directory to write PGN files into. |
| `-j, --concurrency N` | `2` | Number of lessons fetched in parallel. |
| `--delay SECONDS` | `0.4` | Jittered pause after each request, to stay polite. |
| `--limit N` | none | Fetch only the first N lessons (handy for a quick smoke test). |

### Output layout

```
output/
└── <course-slug>/
    ├── all.pgn                       # every lesson concatenated, in course order
    ├── 01-<chapter-slug>/
    │   ├── 001-<lesson-slug>.pgn
    │   └── 002-<lesson-slug>.pgn
    └── 02-<chapter-slug>/
        └── ...
```

Each lesson is a single PGN game. Course and chapter names become the `Event`
and `Round` headers, the lesson's starting position (when not the standard one)
is emitted as a `FEN`/`SetUp` header, and the chess.com lesson id is preserved
in a `ChessComLessonId` header. Board drawings survive as PGN comment commands:
arrows as `[%cal ...]` and highlighted squares as `[%csl ...]`, which Lichess,
ChessBase, and most GUIs render.

### Resuming

Re-running `scrape` with the same `--out` directory skips any lesson whose PGN
file already exists, so a partial run continues where it left off. If Cloudflare
starts challenging requests, the tool backs off exponentially and, if it can't
recover, exits cleanly (code `2`) with everything fetched so far on disk; just
run the same command again later to finish.

## Development

Run the test suite (parser and PGN round-trip tests against captured API
responses in `tests/fixtures/`):

```sh
uv run pytest
```
