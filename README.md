# pgn-dl

Download chess content into annotated PGN files you can study offline, import
into a database, or open in any chess GUI.

pgn-dl is built around sources. Each source knows how to turn one kind of URL
into a collection of PGN games. chess.com courses, chess.com lessons, and
noctie.ai themes and opening books are supported today; the architecture
exists so other sites and other kinds of collection can be added without
disturbing what already works.

> **Use it only for content your own account has access to.** It automates the
> same requests your browser makes when you study a course; it does not bypass
> paywalls or share content.

## Sources

| Key | What it downloads | Login | Browser |
| --- | --- | --- | --- |
| `chesscom:courses` | A chess.com course, one PGN per lesson | yes (`pgn-dl login chesscom`) | yes |
| `chesscom:lessons` | A chess.com lesson course, one PGN per lesson | yes (`pgn-dl login chesscom`) | yes |
| `noctie:opening-books` | A noctie.ai opening book, one PGN game plus one per bookmark | yes (`pgn-dl login noctie`) | login only |
| `noctie:themes` | A noctie.ai theme, one PGN per element | yes (`pgn-dl login noctie`) | login only |

`pgn-dl sources` prints the same list from the running build, along with the URL
pattern each source claims. "Login only" means a browser is used once, during
`login`; every download afterwards is plain HTTPS, with no browser opened at
all. chess.com is the exception, and drives one for downloads too.

### Courses vs. lessons

chess.com ships two different products that both use the word "course", and
pgn-dl supports both. Which one you want decides the subcommand:

- **Courses** are the paid product at `/courses/`, structured
  Course -> Chapter -> Lesson. Use `pgn-dl chesscom courses`.
- **Lessons** are the free product at `/lessons/`, structured
  lesson course -> lesson -> challenge. Use `pgn-dl chesscom lessons`.

The collision runs both ways: the Courses product calls its own leaf units
"lessons" too. chess.com's own term for the Lessons grouping is
`lesson_course` (it appears as `topic-code="lesson_course"` and in asset
URLs), which is why this README says "lesson course" rather than plain
"course" when it means the free product.

### Themes vs. opening books

noctie.ai keeps two different kinds of collection under one login, and they
produce different shapes of PGN:

- **Themes** are a numbered sequence of elements - flashcards, sparring
  positions, instruction pages. Use `pgn-dl noctie themes`; each element
  becomes its own PGN file.
- **Opening books** are a single move tree. Use `pgn-dl noctie
  opening-books`; the whole book becomes one PGN game, its branches written
  as PGN variations. Wherever the author wrote them, per-move commentary
  rides along as PGN move comments and evaluation marks as PGN NAGs, and the
  book's own introduction becomes the game comment. A book can also name
  bookmarks - cuts of that same tree - and each of those is written again as
  its own file, carrying the commentary for its own lines; see
  [Output layout](#output-layout).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Google Chrome, for sources marked "Browser" above

## Setup

```sh
uv sync --extra browser
uv run playwright install chrome   # skip if Chrome is already installed
```

The `browser` extra installs Playwright. It is only needed for sources that
drive a real browser: chess.com's downloads, and noctie's one-time `login
noctie`. A plain `uv sync` is enough for anything served over ordinary HTTP,
which for noctie means every download once you have signed in.

## Usage

### 1. Sign in (once per source that needs it)

```sh
uv run pgn-dl login chesscom
```

A browser window opens. Log in to chess.com, then press Enter in the terminal
to save the session. You only need to repeat this if the session expires.

The session lives in `.profile/chesscom/`, resolved from the current working
directory, so run `login` and downloads from the same place. To keep a session
that works from anywhere, point at a fixed location:

```sh
export PGN_DL_PROFILE=~/.pgn-dl-profile
```

> **If you logged in before sources were split out**, your session is in the old
> `.profile/` root rather than `.profile/chesscom/` and will not be found. Run
> `pgn-dl login chesscom` again; there is no migration.

> **Downloads from before the split moved too**, from `output/<course-slug>/` to
> `output/chesscom/courses/<course-slug>/`; move an old directory across so a
> re-run resumes instead of fetching every lesson again:
>
> ```sh
> mkdir -p output/chesscom/courses
> mv output/<course-slug> output/chesscom/courses/
> ```

noctie's sign-in is different: there is nothing to press Enter for, and what
gets saved is a credential file rather than a browser session.

```sh
uv run pgn-dl login noctie
```

A browser window opens with a choice of email code, Google, or Apple. Sign in,
and the window closes itself as soon as it detects success.

**This writes a long-lived credential to disk.** noctie's own session lives in
a Firebase refresh token rather than a cookie, and `login noctie` reads it out
of the signed-in browser and stores it at `.profile/noctie/credentials.json`
(or `$PGN_DL_PROFILE/noctie/credentials.json`, if that is set), owner-only:
mode `0600`, inside a `0700` directory. From then on every `pgn-dl noctie ...`
download needs no browser at all: it exchanges that stored refresh token for
a Firestore ID token, good for one hour, by posting to Google's
`securetoken.googleapis.com` token endpoint. That token endpoint is the only
place the credential is ever sent.

### 2. Download

Use the source subcommand:

```sh
uv run pgn-dl chesscom courses <course>
```

`<course>` is the course's UUID or any chess.com URL that contains it. The
easiest source is a course's training URL, which has the form
`https://www.chess.com/courses/train/<uuid>`. Pass either the whole URL or just
the UUID:

```sh
uv run pgn-dl chesscom courses https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e
uv run pgn-dl chesscom courses 6445752b-f404-4d8f-9397-4fb03f690d8e
```

Or let pgn-dl work out the source from a URL:

```sh
uv run pgn-dl url https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e
```

Passing a URL with no subcommand is shorthand for `pgn-dl url`, and takes the
same options:

```sh
uv run pgn-dl https://www.chess.com/courses/train/6445752b-f404-4d8f-9397-4fb03f690d8e --limit 5
```

Only arguments that start with `http://` or `https://` are treated this way; a
mistyped subcommand is still reported as an unknown command. If a URL is not
claimed by any source, pgn-dl says so and lists the sources it does know.

Lessons work the same way, with `pgn-dl chesscom lessons` in place of
`pgn-dl chesscom courses`. Give a lesson-course URL to download every lesson in
it, or a two-segment lesson URL to download just one lesson:

```sh
# a whole lesson course (only where chess.com publishes a landing page for it)
uv run pgn-dl chesscom lessons https://www.chess.com/lessons/learn-to-play

# a single lesson (always works)
uv run pgn-dl chesscom lessons https://www.chess.com/lessons/advanced-chess-tactics/in-between-moves

# bare-URL sugar works too
uv run pgn-dl https://www.chess.com/lessons/learn-to-play
```

Not every lesson course has a landing page: `learn-to-play` does,
`advanced-chess-tactics` does not. Downloading the whole `advanced-chess-tactics`
lesson course fails with an actionable error rather than a silent empty result;
its lessons still work individually, fetched by their two-segment URLs as
above.

noctie.ai themes and opening books work the same way: one target per
invocation, by URL or bare UUID.

```sh
uv run pgn-dl noctie themes https://app.noctie.ai/themes/32380699-ec8a-48b4-9b3f-3e0074d74c38
uv run pgn-dl noctie opening-books https://app.noctie.ai/opening-book/fbdadf0d-adac-4d9f-9557-83769b079737

# bare-URL sugar works here too
uv run pgn-dl https://app.noctie.ai/themes/32380699-ec8a-48b4-9b3f-3e0074d74c38
```

noctie's own routes are inconsistent about plurals - themes link as
`/themes/<id>`, opening books as `/opening-book/<id>` - so pgn-dl accepts both
the singular and the plural form of each.

Options, the same for `url` and for every source subcommand:

| Option | Default | Description |
| --- | --- | --- |
| `--out PATH` | `output` | Directory to write PGN files into. |
| `-j, --concurrency N` | `2` | Number of items fetched in parallel. |
| `--delay SECONDS` | `0.4` | Jittered pause after each request, to stay polite. |
| `--limit N` | none | Fetch only the first N items (handy for a quick smoke test). |

### Output layout

Output is namespaced by source and by kind, so two sources can never collide:

```
output/
└── chesscom/
    └── courses/
        └── <course-slug>/
            ├── all.pgn                   # every lesson concatenated, in course order
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
ChessBase, and most GUIs render. A sideline the author wrote inside a comment
becomes a real PGN variation, placed by the move number chess.com writes into
it; one that names no position in the lesson is kept as the sentence it is, on
the move it annotates, rather than guessed at.

Lessons use a flatter layout, since a lesson course has no chapters:

```
output/chesscom/lessons/learn-to-play/
├── all.pgn
├── 001-the-king.pgn        # one file per lesson, one game per challenge
├── 002-the-queen.pgn
└── ...
```

Each lesson file holds one PGN game per challenge. `Round` numbers the
challenge within its lesson, and the lesson's chess.com UUID is preserved in a
`ChessComLessonId` header. As with courses, chess.com's own arrow and
highlight commands are translated to standard `[%cal ...]` / `[%csl ...]`; two
chess.com-specific commands have no standard equivalent and are kept as plain
comment text instead: `%hint`, a pedagogical hint for the move, and `%wrong`,
feedback shown for an incorrect try.

Challenges come in two kinds. Tactical challenges carry a real solution: the
correct line as the PGN mainline, with wrong tries recorded as sibling
variations. Beginner drills - marked with a
`LESSON_CHALLENGE_TYPE "captureTheStar"` header - carry a starting position and
instructions but no moves at all, because that is all chess.com stores for
them.

noctie themes and opening books live under `output/noctie/`:

```
output/
└── noctie/
    ├── themes/
    │   └── beginner-s-guide-to-1-e4-32380699/
    │       ├── all.pgn
    │       ├── 009-sparring-position.pgn
    │       ├── 013-puzzle-3.pgn
    │       ├── 027-puzzle-2.pgn
    │       └── ...                        # gaps are normal, see below
    └── opening-books/
        └── beginner-s-guide-to-1-e4-fbdadf0d/
            ├── all.pgn
            ├── beginner-s-guide-to-1-e4.pgn
            └── bookmarks/
                ├── 01-1-e5-king-s-pawn-game.pgn
                ├── 02-1-c5-sicilian-defense.pgn
                └── ...
```

Both kinds get a collection directory suffixed with a slice of the noctie id,
like `-32380699` above. Theme and opening book names are not unique upstream
(the same title turns up on several different documents), so the id keeps two
different collections from landing in the same output directory. The tree above
is a real example of exactly that: the theme and the opening book are both
called "Beginner's Guide to 1. e4", and only the id suffix tells them apart.

The two kinds' PGNs differ in shape, matching the split described under
[Themes vs. opening books](#themes-vs-opening-books): a theme writes one PGN
file per element, numbered by noctie's own `index` rather than by position in
the list, so gaps in the numbering (`001`, `004`, ...) are expected rather than
a sign of a missing element. An opening book writes a single PGN game, its move
tree rebuilt from the book's own branches into a mainline plus PGN variations -
that is `beginner-s-guide-to-1-e4.pgn` above.

Each move in that tree can carry the author's own commentary, written as a
PGN comment attached to the move, and an evaluation glyph, written as a
standard PGN NAG - observed values are `$1`, `$2`, `$4`, and `$6`, so a
dubious try might read `3... dxe4 $6`. The book's own introduction (its
short description, its longer write-up, and the author's preface on the
tree's root position) is composed into the game comment, in that order,
separated by blank lines, skipping whichever pieces the book does not have.
None of this is guaranteed: a book only carries what its author wrote, and
that is often incomplete. On the sample book, 189 of its 265 moves carry a
comment; on a longer Caro-Kann repertoire, only 316 of its 1503 nodes do. Some
books carry no commentary at all.

A book can also carry bookmarks: named cuts of that same tree. Each one is
written again as its own file under `bookmarks/`, numbered by its order in the
book. A bookmark file opens with the moves that lead to it, replayed from the
start, and then contains everything below it, so it reads as a complete game
rather than a fragment: on the sample book, bookmark "1...e5 - King's Pawn
Game" names the node at `> e4 e5`, so `01-1-e5-king-s-pawn-game.pgn` opens
`1. e4 e5` and continues into that whole branch of the repertoire. The replayed
moves keep whatever the author wrote about them, so a paragraph attached to
`1. e4` appears in every bookmark that plays through it. A book with no
bookmarks has no `bookmarks/` directory at all.

Because a bookmark is a slice of the same tree, its moves keep whatever
commentary and NAGs they carried in the whole-book file. A bookmark does not
repeat the whole book's introduction, though: it keeps its own short
description instead, the one written for that bookmark specifically.

Because of this, `all.pgn` contains each line at least twice: once inside the
whole-book game, and again inside every bookmark file that covers it - exactly
twice on the sample book, whose eight bookmarks are one per reply to 1.e4 and so
partition it, and more often wherever bookmarks nest or overlap. `all.pgn` is
the concatenation of everything under the collection directory, bookmarks
included.

Some content is paywalled on noctie's side: themes and opening books carry a
`requiresTier` field, and access is enforced by noctie's own Firestore
security rules, not by pgn-dl. The denial lands on the theme or opening book
document itself, which is read before anything can be listed, so on an account
without the right tier the whole run stops there and nothing is downloaded:

```
! Failed before any item could be listed: <url> failed: 403 PERMISSION_DENIED;
  this theme may require a paid noctie tier, or may not be shared with this
  account. Access is enforced by noctie, not by this tool.
  Nothing was downloaded. Re-run the same command to resume.
```

The exit code is `2`, and "opening book" replaces "theme" when that is what
failed. Signing in again does not change the answer, which is why the command
does not suggest it here. A single element of an otherwise readable theme can be
denied the same way, and that fails just that item.

A PGN comment is delimited by `{` and `}`, and the format has no way to
escape either character inside one, so pgn-dl writes a literal `{` as `(` and
a literal `}` as `)` wherever they occur in comment text. The substitution
lives in the shared PGN writer, so it covers every source that hands pgn-dl a
move tree to render - noctie's book commentary and chess.com's course
annotations. chess.com lessons are the exception: they arrive as PGN already and
are re-exported as they came, so nothing there is substituted, which costs
nothing in practice because a comment in valid upstream PGN cannot contain a raw
`}` to begin with. Left alone, python-chess's own exporter drops the closing
brace and corrupts the sentence around it. The substitution is not reversible:
given a `(` in a downloaded PGN, there is no way to tell whether the author
wrote a parenthesis or pgn-dl converted a brace.

### Resuming

Re-running the same command with the same `--out` directory skips any item whose
PGN file already exists, so a partial run continues where it left off. Every
item is written as soon as it is parsed, so an interrupted run loses at most the
in-flight requests.

Chess.com sits behind Cloudflare, which is why that source drives an actual
Chrome profile instead of raw HTTP: the browser's cookies and TLS fingerprint
are what get the API requests accepted. If Cloudflare starts challenging
requests anyway, pgn-dl backs off exponentially and, if it cannot recover, exits
cleanly (code `2`) with everything fetched so far on disk; run the same command
again later to finish.

noctie mints one Firestore ID token at the start of a run and reuses it for
every item; that token is good for one hour. A run that outlives it does not
stop there: a read refused as expired re-mints the token and is retried once,
and the rest of the run carries on with the new one. However many reads are in
flight when the hour passes, they share a single re-mint between them. A read
still refused while holding a token seconds old is a real authorization
failure rather than an expiry, and fails just that item. Nothing is written for
it, so re-running the same command afterwards picks up exactly where it stopped.

## Adding a source

`src/pgn_dl/sources/chesscom/` is the worked example. A source provides three
things:

1. **An `Extractor` subclass** (`core/extractor.py`) with a `key` of the form
   `"<source>:<kind>"`. Subclassing registers it, so nothing else needs editing
   except the import in `src/pgn_dl/sources/__init__.py`.
2. **`discover(fetcher)`**, returning the collection's metadata and every item's
   output path without fetching anything. Knowing all paths up front is what
   makes resume free.
3. **`fetch(fetcher, ref)`**, returning one parsed item.

Optional hooks: `url_patterns` opts the source into `pgn-dl url` and the
bare-URL form (`from_url()` builds the extractor from a matching URL, declining
one whose target will not parse; override it only if that is not the right
answer); `needs_browser` selects the Playwright transport, with the session
kept under `.profile/<source>` unless `browser_profile()` is overridden; and
a `typer.Typer` sub-app defined in the source's own package and registered
there with `register_app("<source>", app)` (see `sources/chesscom/__init__.py`)
gives the source its own subcommand - `cli.py` mounts whatever the registry
holds, so it needs no edit either.

Sources talk to a `Fetcher` and never to HTTP directly, so the same extractor
runs over plain HTTP or through a browser.

## Development

```sh
uv run pytest
uvx ruff check
uvx ty check
```
