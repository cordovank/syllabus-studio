"""The published reader as plain files (spec 003).

A site is the reader's own pages plus ``catalog.json`` plus one bundle per course.
Readers open it from any static host; nothing in it calls ``/api/v1``.

The same two generators — :func:`site_catalog` and :func:`site_bundle` — feed both
``syllabus-studio site build`` and the server's ``/reader/`` preview, so what the
author previews is what a build writes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from syllabus_studio.core.tutor import available_lenses

from .base import CourseStore
from .bundle import CourseBundle

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Every file the reader needs, as (source under web/, path in the site). Explicit
# rather than "copy static/": the Studio's modules (api.js, studio.js, library.js)
# must never ship, and a test follows the reader's imports to keep this complete.
READER_FILES: tuple[tuple[str, str], ...] = (
    ("reader.html", "index.html"),
    ("static/css/app.css", "static/css/app.css"),
    ("static/js/reader.js", "static/js/reader.js"),
    ("static/js/data.js", "static/js/data.js"),
    ("static/js/lesson.js", "static/js/lesson.js"),
    ("static/js/rail.js", "static/js/rail.js"),
    ("static/js/tutor.js", "static/js/tutor.js"),
    ("static/js/flashcards.js", "static/js/flashcards.js"),
    ("static/js/markup.js", "static/js/markup.js"),
    ("static/js/state.js", "static/js/state.js"),
)

COURSES_DIR = "courses"

# Course ids are ours (slug + suffix), but a file name is still checked before it
# touches the filesystem or a route.
_BUNDLE_FILE = re.compile(r"^(?P<id>[A-Za-z0-9][A-Za-z0-9_-]*)\.course\.json$")


def bundle_filename(course_id: str) -> str:
    """By id, not title: two courses may share a title, never an id."""
    return f"{course_id}.course.json"


def course_id_from_filename(name: str) -> str | None:
    m = _BUNDLE_FILE.match(name)
    return m.group("id") if m else None


async def site_bundle(store: CourseStore, course_id: str) -> CourseBundle | None:
    """What a reader downloads for one course: outline and written lessons, no progress."""
    course = await store.get_course(course_id)
    if course is None:
        return None
    lessons = {}
    for lesson_id in await store.list_built_lessons(course.id):
        content = await store.get_lesson(course.id, lesson_id)
        if content is not None:
            lessons[lesson_id] = content
    return CourseBundle.build(course, lessons)


async def site_catalog(store: CourseStore) -> dict[str, Any]:
    """``catalog.json`` for every stored course, bundle URLs relative to the catalog.

    Carries the lens labels too: the reader has no ``/lenses`` endpoint to ask.
    """
    entries = []
    for summary in await store.list_courses():
        course = await store.get_course(summary.id)
        if course is None:
            continue
        entries.append(
            {
                "id": course.id,
                "title": course.title,
                "description": course.subtitle,
                "url": f"{COURSES_DIR}/{bundle_filename(course.id)}",
                "lessonCount": len(course.all_lessons()),
                "tags": [],
            }
        )
    return {"name": "Courses", "lenses": available_lenses(), "entries": entries}


@dataclass
class SiteReport:
    out_dir: Path
    courses: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _write_atomic(path: Path, text: str) -> None:
    """Temp file then rename, so a failed build never leaves half a catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        # mkstemp creates 0600; a site is public files, readable by whatever serves them.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


async def build_site(store: CourseStore, out_dir: Path) -> SiteReport:
    """Write the reader, the catalog and every course bundle into ``out_dir``.

    Never deletes a file it didn't write: a ``CNAME`` or anything else the author
    put there survives. The one exception is ``courses/``, which is ours — a
    bundle whose course is gone is removed so it can't be installed by URL.
    """
    out_dir = Path(out_dir)
    report = SiteReport(out_dir=out_dir)

    # Generate everything before touching the site, so a storage error changes nothing.
    catalog = await site_catalog(store)
    bundles: dict[str, str] = {}
    for entry in catalog["entries"]:
        bundle = await site_bundle(store, entry["id"])
        if bundle is not None:
            bundles[bundle_filename(entry["id"])] = bundle.to_json()

    for source, dest in READER_FILES:
        target = out_dir / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WEB_DIR / source, target)

    # GitHub Pages runs Jekyll unless told not to, and Jekyll drops some files.
    (out_dir / ".nojekyll").touch()

    courses = out_dir / COURSES_DIR
    courses.mkdir(parents=True, exist_ok=True)
    for name, text in bundles.items():
        _write_atomic(courses / name, text)
        report.courses.append(name)

    for stale in sorted(courses.glob("*.course.json")):
        if stale.name not in bundles:
            stale.unlink()
            report.removed.append(stale.name)

    # Last, so the catalog never names a bundle that isn't on disk yet.
    _write_atomic(out_dir / "catalog.json", json.dumps(catalog, indent=2, ensure_ascii=False))
    return report
