"""End-to-end scrape: course id to a directory tree of PGN files.

Designed to survive Cloudflare interruptions:

- Each lesson is written to disk as soon as it's parsed, so a crash loses at
  most the in-flight requests.
- A second run skips lessons whose PGN file already exists, so the scrape
  resumes naturally.
- Cloudflare challenge responses trigger exponential backoff with jitter;
  after persistent failures we stop cleanly with what we have.
"""

from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from pathlib import Path

import typer

from . import parser, pgn
from .auth import browser_context
from .client import CloudflareBlocked, CoursesClient
from .models import Chapter

_MAX_ATTEMPTS = 5
_BASE_BACKOFF = 4.0  # seconds; doubled per attempt


async def _fetch_with_retry(
    client: CoursesClient,
    variation_id: str,
    name: str,
) -> dict | None:
    """Fetch one lesson, backing off on Cloudflare blocks. Returns None if we give up."""
    backoff = _BASE_BACKOFF
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await client.get_lesson(variation_id)
        except CloudflareBlocked:
            if attempt == _MAX_ATTEMPTS:
                typer.echo(
                    f"  ! Cloudflare blocked after {attempt} attempts on '{name}'; "
                    "stopping. Re-run later to resume."
                )
                return None
            wait = backoff + random.uniform(0, backoff / 2)
            typer.echo(
                f"  ~ Cloudflare challenge on '{name}'; sleeping {wait:.1f}s "
                f"(attempt {attempt}/{_MAX_ATTEMPTS})"
            )
            await asyncio.sleep(wait)
            backoff *= 2
    return None


async def scrape_course(
    course_id: str,
    out: Path,
    concurrency: int = 2,
    delay: float = 0.4,
    limit: int | None = None,
) -> int:
    """Fetch every lesson of ``course_id`` and write PGN files under ``out``."""
    async with browser_context(headless=True) as ctx:
        client = CoursesClient(ctx)
        await client.authenticate()
        typer.echo("Authenticated. Fetching course outline...")

        session_raw = await client.get_learning_session(course_id)
        course, lesson_headers = parser.parse_course_skeleton(session_raw)
        typer.echo(
            f'Course: "{course.name}" - {len(course.chapters)} chapters, '
            f"{len(lesson_headers)} lessons"
        )
        if limit is not None:
            lesson_headers = lesson_headers[:limit]
            typer.echo(f"(limiting to first {limit} lessons)")

        chapters_by_id = {c.id: c for c in course.chapters}
        per_chapter_counters: dict[str, int] = defaultdict(int)

        # Assign final idx + path to each header up-front so resume works.
        plan: list[tuple[dict, Chapter, int, Path]] = []
        for header in lesson_headers:
            ch_id = header.get("chapterId")
            if ch_id is None:
                continue
            chapter = chapters_by_id.get(ch_id)
            if chapter is None:
                continue
            per_chapter_counters[ch_id] += 1
            idx = per_chapter_counters[ch_id]
            path = pgn.lesson_path(
                out, course, chapter, idx, header.get("name", "lesson")
            )
            plan.append((header, chapter, idx, path))

        sem = asyncio.Semaphore(concurrency)
        written = 0
        skipped = 0
        failed = 0
        stopped = False

        async def process(header: dict, chapter: Chapter, idx: int, path: Path) -> None:
            nonlocal written, skipped, failed, stopped
            if stopped:
                return
            if path.exists():
                skipped += 1
                return
            name = header.get("name", "")
            async with sem:
                if stopped:
                    return
                try:
                    raw = await _fetch_with_retry(client, header["id"], name)
                except Exception as exc:  # noqa: BLE001
                    # An unexpected error on one lesson must not abort the batch;
                    # leave it unwritten so a re-run retries it.
                    failed += 1
                    typer.echo(f"  ! Skipping {name or path.name!r}: {exc!r}")
                    return
                if raw is None:
                    stopped = True
                    return
                if delay:
                    await asyncio.sleep(delay + random.uniform(0, delay / 2))

            try:
                lesson = parser.parse_lesson(raw, idx=idx)
                pgn.write_text_atomic(
                    path, pgn.lesson_to_pgn(course, chapter, lesson) + "\n"
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                typer.echo(f"  ! Skipping {name or path.name!r}: {exc!r}")
                return
            written += 1
            done = written + skipped
            typer.echo(f"  [{done:>4}/{len(plan)}] {lesson.name}")

        await asyncio.gather(*(process(h, c, i, p) for h, c, i, p in plan))

    summary = f"Done. Wrote {written}, skipped {skipped} already-present"
    if failed:
        summary += f", failed {failed}"
    summary += f" (of {len(plan)} lessons)."
    typer.echo(summary)

    course_out = pgn.course_dir(out, course)
    if course_out.is_dir():
        combined = pgn.combine_course_dir(course_out)
        typer.echo(f"Combined PGN: {combined}")

    if stopped:
        typer.echo(
            "Stopped early due to Cloudflare. Re-run the same command to resume."
        )
        raise typer.Exit(code=2)
    if failed:
        typer.echo(
            f"{failed} lesson(s) failed and were left unwritten; "
            "re-run the same command to retry them."
        )
    return written
