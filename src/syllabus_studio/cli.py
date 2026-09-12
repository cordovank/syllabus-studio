"""Command line entry point.

    syllabus-studio serve
    syllabus-studio build path/to/syllabus.txt --name "Applied ML"
    syllabus-studio export <course-id> -o applied-ml.course.json
    syllabus-studio import applied-ml.course.json
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
from syllabus_studio.core.builder import build_course
from syllabus_studio.core.models import LessonProgress
from syllabus_studio.llm import get_provider
from syllabus_studio.storage import CourseBundle, get_store, suggested_filename


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
    provider = get_provider(settings)
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
    if args.command == "import":
        return asyncio.run(_with_store(lambda s, st: _cmd_import(s, st, path=args.path)))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
