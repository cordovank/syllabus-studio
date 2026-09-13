"""The published reader as plain files (spec 003).

A site is the reader's own pages plus ``catalog.json`` plus one bundle per course.
Readers open it from any static host; nothing in it calls ``/api/v1``.

The same generators feed ``syllabus-studio site build`` and the server's reader:

- ``/reader/`` serves the site as it is on disk (:func:`read_site_catalog`).
- ``/reader/preview/<id>/`` serves that catalog with one course upserted as it would
  be published now (:func:`preview_catalog`). The reader fetches ``catalog.json``
  relative to its page, so the URL alone selects the preview — no reader code
  knows it is being previewed beyond the entry's ``preview`` flag.
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

from syllabus_studio.core.authoring import enriched_passes
from syllabus_studio.core.models import now_ms
from syllabus_studio.core.tutor import available_lenses

from .base import CourseStore
from .bundle import CourseBundle, Provenance

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


def valid_course_id(course_id: str) -> bool:
    return course_id_from_filename(bundle_filename(course_id)) == course_id


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


def catalog_entry(bundle: CourseBundle, *, with_provenance: bool = False) -> dict[str, Any]:
    """One catalog card. The provenance mirrors let a reader judge a course before
    downloading it; they are only written when the bundle's provenance was stamped,
    since an absent field means "the catalog didn't say", not "not reviewed"."""
    course = bundle.course
    entry: dict[str, Any] = {
        "id": course.id,
        "title": course.title,
        "description": course.subtitle,
        "url": f"{COURSES_DIR}/{bundle_filename(course.id)}",
        "lessonCount": len(course.all_lessons()),
        "tags": [],
    }
    if with_provenance:
        p = bundle.provenance
        entry |= {
            "authorModel": p.author_model,
            "humanReviewed": p.human_reviewed,
            "enriched": list(p.enriched),
        }
    return entry


def empty_catalog() -> dict[str, Any]:
    return {"name": "Courses", "lenses": available_lenses(), "entries": []}


async def site_catalog(store: CourseStore) -> dict[str, Any]:
    """``catalog.json`` for every stored course, bundle URLs relative to the catalog.

    Carries the lens labels too: the reader has no ``/lenses`` endpoint to ask.
    """
    catalog = empty_catalog()
    for summary in await store.list_courses():
        bundle = await site_bundle(store, summary.id)
        if bundle is not None:
            catalog["entries"].append(catalog_entry(bundle))
    return catalog


def read_site_catalog(site_dir: Path) -> dict[str, Any]:
    """The site's ``catalog.json`` as it is on disk, or an empty one.

    Lens labels are always the current ones: the reader serving it is current code.
    """
    try:
        raw = json.loads((Path(site_dir) / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_catalog()
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return empty_catalog()
    return {
        "name": raw.get("name") or "Courses",
        "lenses": available_lenses(),
        "entries": [e for e in raw["entries"] if isinstance(e, dict) and e.get("id")],
    }


# Fields the author may have edited by hand in catalog.json; a republish keeps them.
_AUTHOR_OWNED = ("description", "tags", "license")


def upsert_entry(catalog: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Replace the entry with the same id, keeping author-set fields and its position,
    or add it first so a new course is the first thing on the page. Returns a copy."""
    entries = list(catalog["entries"])
    for i, existing in enumerate(entries):
        if existing.get("id") == entry["id"]:
            kept = {k: existing[k] for k in _AUTHOR_OWNED if existing.get(k)}
            entries[i] = {**entry, **kept}
            break
    else:
        entries.insert(0, entry)
    return {**catalog, "entries": entries}


def site_bundle_path(site_dir: Path, name: str) -> Path | None:
    """A bundle already in the site, by a checked file name."""
    if course_id_from_filename(name) is None:
        return None
    path = Path(site_dir) / COURSES_DIR / name
    return path if path.is_file() else None


# --- preview ------------------------------------------------------------------


def stamp_provenance(
    bundle: CourseBundle, *, author_provider: str, author_model: str, reviewer: str = ""
) -> CourseBundle:
    """Provenance as publishing would write it now. Returns a copy."""
    stamped = bundle.model_copy(deep=True)
    stamped.provenance = Provenance(
        author_provider=author_provider,
        author_model=author_model,
        depth=bundle.course.depth,
        generated_at=now_ms(),
        enriched=enriched_passes(bundle.lessons.values()),
        human_reviewed=bool(reviewer),
        reviewer=reviewer,
    )
    return stamped


async def preview_bundle(
    store: CourseStore, course_id: str, *, author_provider: str, author_model: str
) -> CourseBundle | None:
    bundle = await site_bundle(store, course_id)
    if bundle is None:
        return None
    return stamp_provenance(bundle, author_provider=author_provider, author_model=author_model)


async def preview_catalog(
    store: CourseStore,
    site_dir: Path,
    course_id: str,
    *,
    author_provider: str,
    author_model: str,
) -> dict[str, Any] | None:
    """The site's catalog as it would be if this course were published now.

    Read-only: the site on disk is never written. The course's entry carries
    ``"preview": true``, which is what makes the reader show its preview strip —
    a built site never has one, so the reader needs no separate preview mode.
    """
    bundle = await preview_bundle(
        store, course_id, author_provider=author_provider, author_model=author_model
    )
    if bundle is None:
        return None
    entry = catalog_entry(bundle, with_provenance=True) | {"preview": True}
    return upsert_entry(read_site_catalog(site_dir), entry)


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
