"""Command line entry point.

    syllabus-studio serve
    syllabus-studio build path/to/syllabus.txt --name "Applied ML"
    syllabus-studio export <course-id> -o applied-ml.course.json
    syllabus-studio import applied-ml.course.json
    syllabus-studio enrich <course-id> [--lenses] [--faq] [--force]
    syllabus-studio publish <course-id> -o applied-ml.course.json [--reviewed-by NAME]
    syllabus-studio list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from syllabus_studio import __version__
from syllabus_studio.config import get_settings
from syllabus_studio.core.authoring import enrich_course, enriched_passes
from syllabus_studio.core.builder import build_course
from syllabus_studio.core.lessons import LessonError, write_lesson
from syllabus_studio.core.models import LessonProgress, now_ms
from syllabus_studio.llm import get_provider
from syllabus_studio.storage import CourseBundle, Provenance, get_store, suggested_filename


async def _with_store(fn):  # noqa: ANN001, ANN202
    settings = get_settings()
    store = get_store(settings)
    await store.startup()
    try:
        return await fn(settings, store)
    finally:
        await store.shutdown()


async def _cmd_list(_settings, store) -> int:  # noqa: ANN001
    rows = await store.list_courses()
    if not rows:
        print("No courses yet. Build one:  syllabus-studio build syllabus.txt")
        return 0
    width = max(len(r.id) for r in rows)
    for r in rows:
        print(f"{r.id:<{width}}  {r.done_count:>3}/{r.lesson_count:<3}  {r.title}")
    return 0


async def _cmd_build(settings, store, *, path: Path, name: str, depth: str) -> int:  # noqa: ANN001
    syllabus = path.read_text(encoding="utf-8")
    provider = get_provider(settings, role="author")
    course = await build_course(provider, syllabus=syllabus, name=name, depth=depth)
    await store.save_course(course)
    done, total = course.stats()
    print(f"{course.id}  —  {course.title}  ({len(course.modules)} modules, {total} lessons)")
    return 0


async def _cmd_export(_settings, store, *, course_id: str, out: Path | None) -> int:  # noqa: ANN001
    course = await store.get_course(course_id)
    if course is None:
        print(f"No course {course_id!r}", file=sys.stderr)
        return 1
    lessons = {}
    for lid in await store.list_built_lessons(course.id):
        content = await store.get_lesson(course.id, lid)
        if content:
            lessons[lid] = content
    bundle = CourseBundle.build(course, lessons)
    target = out or Path(suggested_filename(course))
    bundle.write(target)
    print(f"Wrote {target}  ({len(lessons)} written lessons)")
    return 0


async def _cmd_import(_settings, store, *, path: Path) -> int:  # noqa: ANN001
    bundle = CourseBundle.parse(json.loads(path.read_text(encoding="utf-8")))
    course = bundle.materialise()
    await store.save_course(course)
    for lid, content in bundle.lessons.items():
        if course.find(lid) is None:
            continue
        await store.save_lesson(course.id, lid, content)
        course.progress.setdefault(lid, LessonProgress()).built = True
    await store.save_course(course)
    print(f"{course.id}  —  {course.title}")
    return 0


async def _run_enrich(provider, store, course, *, lenses: bool, faq: bool, force: bool) -> int:  # noqa: ANN001
    """Print one line per lesson; return how many did not end fully enriched."""
    shortfall = 0
    async for p in enrich_course(
        provider, store, course=course, lenses=lenses, faq=faq, force=force
    ):
        stage = p["stage"]
        if stage == "started":
            continue
        detail = ""
        if stage == "partial":
            detail = f"  missing: {', '.join(p['missing'])}"
            shortfall += 1
        elif stage == "failed":
            detail = f"  {p['code']}: {p['message']}"
            shortfall += 1
        print(f"  [{p['done']:>3}/{p['total']}] {p['lessonId']:<8} {stage}{detail}")
    return shortfall


async def _cmd_enrich(  # noqa: ANN001
    settings, store, *, course_id: str, lenses: bool, faq: bool, force: bool
) -> int:
    course = await store.get_course(course_id)
    if course is None:
        print(f"No course {course_id!r}", file=sys.stderr)
        return 1
    # Neither flag means both: the flags narrow, they don't opt in.
    if not lenses and not faq:
        lenses = faq = True
    provider = get_provider(settings, role="author")
    shortfall = await _run_enrich(provider, store, course, lenses=lenses, faq=faq, force=force)
    if shortfall:
        print(f"{shortfall} lesson(s) incomplete — re-run to fill only what is missing.")
    return 1 if shortfall else 0


async def _cmd_publish(  # noqa: ANN001
    settings, store, *, course_id: str, out: Path | None, reviewed_by: str, force: bool
) -> int:
    """The contributor's one command: write missing lessons, enrich, stamp, export."""
    course = await store.get_course(course_id)
    if course is None:
        print(f"No course {course_id!r}", file=sys.stderr)
        return 1
    provider = get_provider(settings, role="author")
    model = settings.model_for_tier("default", provider.name)

    # 1. every lesson written — a catalog course with holes in it isn't publishable
    built = set(await store.list_built_lessons(course.id))
    missing = [ls for _, _, ls in course.all_lessons() if ls.id not in built]
    for n, lesson in enumerate(missing, start=1):
        print(f"  writing [{n}/{len(missing)}] {lesson.id}  {lesson.title}")
        try:
            content = await write_lesson(
                provider, course=course, lesson_id=lesson.id, model_name=model
            )
        except LessonError as exc:
            print(f"Could not write {lesson.id}: {exc.code}: {exc.message}", file=sys.stderr)
            print("Nothing exported. Re-run publish; written lessons are kept.", file=sys.stderr)
            return 1
        await store.save_lesson(course.id, lesson.id, content)
        await store.set_progress(course.id, lesson.id, LessonProgress(built=True))

    # 2. enrich; a shortfall is reported, and provenance below says exactly what's there
    print("  enriching")
    shortfall = await _run_enrich(provider, store, course, lenses=True, faq=True, force=force)

    # 3. stamp and export
    lessons = {}
    for _, _, ls in course.all_lessons():
        stored = await store.get_lesson(course.id, ls.id)
        if stored is not None:
            lessons[ls.id] = stored
    bundle = CourseBundle.build(course, lessons)
    bundle.provenance = Provenance(
        author_provider=provider.name,
        author_model=model,
        depth=course.depth,
        generated_at=now_ms(),
        enriched=enriched_passes(lessons.values()),
        human_reviewed=bool(reviewed_by),
        reviewer=reviewed_by,
    )
    target = out or Path(suggested_filename(course))
    bundle.write(target)

    print(f"Wrote {target}  ({len(lessons)} lessons, enriched: "
          f"{', '.join(bundle.provenance.enriched) or 'none'})")
    if shortfall:
        print(f"{shortfall} lesson(s) not fully enriched; re-run publish to fill the gaps.")
    if not reviewed_by:
        print("Marked as not human-reviewed. Pass --reviewed-by NAME once someone has read it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="syllabus-studio", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the web app")
    sub.add_parser("list", help="list stored courses")

    p_build = sub.add_parser("build", help="build a course from a syllabus file")
    p_build.add_argument("path", type=Path)
    p_build.add_argument("--name", default="")
    p_build.add_argument("--depth", default="standard", choices=["new", "standard", "deep"])

    p_export = sub.add_parser("export", help="write a portable course bundle")
    p_export.add_argument("course_id")
    p_export.add_argument("-o", "--out", type=Path)

    p_import = sub.add_parser("import", help="install a course bundle")
    p_import.add_argument("path", type=Path)

    p_enrich = sub.add_parser("enrich", help="precompute lenses and FAQ for written lessons")
    p_enrich.add_argument("course_id")
    p_enrich.add_argument("--lenses", action="store_true", help="only lenses")
    p_enrich.add_argument("--faq", action="store_true", help="only the FAQ")
    p_enrich.add_argument("--force", action="store_true", help="redo what already exists")

    p_publish = sub.add_parser("publish", help="write, enrich, stamp provenance and export")
    p_publish.add_argument("course_id")
    p_publish.add_argument("-o", "--out", type=Path)
    p_publish.add_argument("--reviewed-by", default="", metavar="NAME")
    p_publish.add_argument("--force", action="store_true", help="redo existing enrichment")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from syllabus_studio.__main__ import main as serve

        serve()
        return 0
    if args.command == "list":
        return asyncio.run(_with_store(_cmd_list))
    if args.command == "build":
        return asyncio.run(
            _with_store(
                lambda s, st: _cmd_build(s, st, path=args.path, name=args.name, depth=args.depth)
            )
        )
    if args.command == "export":
        return asyncio.run(
            _with_store(lambda s, st: _cmd_export(s, st, course_id=args.course_id, out=args.out))
        )
    if args.command == "enrich":
        return asyncio.run(
            _with_store(
                lambda s, st: _cmd_enrich(
                    s, st, course_id=args.course_id, lenses=args.lenses, faq=args.faq,
                    force=args.force,
                )
            )
        )
    if args.command == "publish":
        return asyncio.run(
            _with_store(
                lambda s, st: _cmd_publish(
                    s, st, course_id=args.course_id, out=args.out,
                    reviewed_by=args.reviewed_by, force=args.force,
                )
            )
        )
    if args.command == "import":
        return asyncio.run(_with_store(lambda s, st: _cmd_import(s, st, path=args.path)))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
