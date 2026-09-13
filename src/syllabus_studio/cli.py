"""Command line entry point.

    syllabus-studio serve
    syllabus-studio build path/to/syllabus.txt --name "Applied ML"
    syllabus-studio export <course-id> -o applied-ml.course.json
    syllabus-studio import applied-ml.course.json
    syllabus-studio enrich <course-id> [--lenses] [--faq] [--force]
    syllabus-studio publish <course-id> [--reviewed-by NAME] [-o copy.course.json]
    syllabus-studio unpublish <course-id>
    syllabus-studio list
    syllabus-studio site build [-o site/]
    syllabus-studio site init
    syllabus-studio deploy [--dry-run] [--yes] [-m MESSAGE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from syllabus_studio import __version__
from syllabus_studio.config import get_settings
from syllabus_studio.core.authoring import enrich_course
from syllabus_studio.core.builder import build_course
from syllabus_studio.core.models import LessonProgress
from syllabus_studio.deploy import DeployError, deploy, plan_deploy, site_init
from syllabus_studio.llm import get_provider
from syllabus_studio.publishing import PublishError, author_model_name, publish_course
from syllabus_studio.storage import CourseBundle, get_store, suggested_filename
from syllabus_studio.storage.site import read_site_catalog, unpublish, write_reader_files


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
    """The contributor's one command: write missing lessons, enrich, stamp, and write
    the course into the site. Nothing goes live until the site is deployed."""
    course = await store.get_course(course_id)
    if course is None:
        print(f"No course {course_id!r}", file=sys.stderr)
        return 1
    provider = get_provider(settings, role="author")
    if not provider.describe().get("available", True):
        print("Publishing needs an authoring model; set SS_AUTHOR_PROVIDER.", file=sys.stderr)
        return 1

    report: dict = {}
    try:
        async for event, payload in publish_course(
            provider,
            store,
            course=course,
            site_dir=settings.site_dir,
            model_name=author_model_name(provider),
            reviewer=reviewed_by,
            force=force,
        ):
            if event == "published":
                report = payload
            else:
                _print_progress(payload)
    except PublishError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1

    print(f"Published {report['title']} into {report['siteDir']}/{report['bundle']}")
    print(f"  enriched: {', '.join(report['enriched']) or 'none'}")
    for key, label in (
        ("unwritten", "not written"),
        ("missingLenses", "missing lenses"),
        ("missingFaq", "missing a FAQ"),
    ):
        if report[key]:
            print(f"  warning: {len(report[key])} lesson(s) {label}: {', '.join(report[key])}")
    if not report["humanReviewed"]:
        print("  warning: not human-reviewed; pass --reviewed-by NAME once someone has read it")

    if out is not None:
        bundle = CourseBundle.read(Path(report["siteDir"]) / report["bundle"])
        bundle.write(out)
        print(f"  also wrote {out}")
    print("Nothing is live yet: deploy the site to publish it.")
    return 0


def _print_progress(p: dict) -> None:
    if p["phase"] == "write":
        if p["stage"] == "writing":
            print(f"  writing [{p['done'] + 1}/{p['total']}] {p['lessonId']}")
        return
    stage = p["stage"]
    if stage == "started":
        return
    detail = ""
    if stage == "partial":
        detail = f"  missing: {', '.join(p['missing'])}"
    elif stage == "failed":
        detail = f"  {p['code']}: {p['message']}"
    print(f"  enrich  [{p['done']:>3}/{p['total']}] {p['lessonId']:<8} {stage}{detail}")


def _cmd_unpublish(settings, *, course_id: str) -> int:  # noqa: ANN001
    if not unpublish(settings.site_dir, course_id):
        print(f"{course_id!r} isn't published in {settings.site_dir}", file=sys.stderr)
        return 1
    print(f"Removed {course_id} from {settings.site_dir}. Deploy the site to take it down.")
    return 0


def _cmd_site_init(settings) -> int:  # noqa: ANN001
    try:
        result = site_init(
            settings.site_dir, branch=settings.site_branch, remote=settings.site_remote
        )
    except DeployError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    for line in result.done:
        print(f"  {line}")
    print("Nothing was pushed.")
    print(result.pages_hint)
    return 0


def _describe_card(card: dict) -> str:
    lessons = f"{card['lessonCount']} lessons" if card.get("lessonCount") else "?"
    review = {True: "reviewed", False: "NOT human-reviewed"}.get(card.get("humanReviewed"), "")
    return " · ".join(x for x in (lessons, review) if x)


def _cmd_deploy(settings, *, dry_run: bool, yes: bool, message: str) -> int:  # noqa: ANN001
    where = {"branch": settings.site_branch, "remote": settings.site_remote}
    try:
        plan = plan_deploy(settings.site_dir, **where)
    except DeployError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1

    print(f"Site: {plan['siteDir']}  →  {plan['remote']}/{plan['branch']}")
    width = max(
        (len(c["title"]) for k in ("added", "updated", "removed") for c in plan[k]), default=0
    )
    for kind in ("added", "updated", "removed"):
        for card in plan[kind]:
            detail = "" if kind == "removed" else _describe_card(card)
            print(f"  {kind:<8} {card['title']:<{width}}   {detail}")
    if plan["assets"]:
        print(f"  assets   {plan['assets']} file(s) changed")
    if plan["unpushedCommits"] and not plan["pendingFiles"]:
        print(f"  {plan['unpushedCommits']} commit(s) waiting to be pushed")
    for title in plan["unreviewed"]:
        print(f"  warning: {title} is not human-reviewed; readers will see that")
    print(
        f"Will go live at {plan['url']}" if plan["url"] else "No GitHub Pages URL for this remote."
    )

    if dry_run:
        print("Dry run: nothing committed or pushed.")
        return 0
    if not yes:
        try:
            answer = input("Deploy? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Not deployed.")
            return 1

    try:
        done = deploy(settings.site_dir, message=message, **where)
    except DeployError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    print(f"Pushed {done['commit'][:7]}. Pages usually updates within a minute.")
    return 0


def _cmd_site_build(settings, *, out: Path | None) -> int:  # noqa: ANN001
    site_dir = out or settings.site_dir
    written = write_reader_files(site_dir)
    courses = len(read_site_catalog(site_dir)["entries"])
    print(f"Refreshed the reader in {site_dir}  ({len(written)} files, {courses} courses)")
    print("Courses enter the site by publishing:  syllabus-studio publish <course-id>")
    print(f"Open it:  python -m http.server -d {site_dir}  →  http://localhost:8000/")
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

    p_publish = sub.add_parser(
        "publish", help="write, enrich, stamp provenance and write the course into the site"
    )
    p_publish.add_argument("course_id")
    p_publish.add_argument("-o", "--out", type=Path, help="also write a copy of the bundle here")
    p_publish.add_argument("--reviewed-by", default="", metavar="NAME")
    p_publish.add_argument("--force", action="store_true", help="redo existing enrichment")

    p_unpublish = sub.add_parser("unpublish", help="remove a course from the site")
    p_unpublish.add_argument("course_id")

    p_site = sub.add_parser("site", help="the published reader: static files for any host")
    site_sub = p_site.add_subparsers(dest="site_command", required=True)
    p_site_build = site_sub.add_parser("build", help="refresh the reader; publishes nothing")
    p_site_build.add_argument("-o", "--out", type=Path, help="default: SS_SITE_DIR (./site)")
    site_sub.add_parser(
        "init", help="make SS_SITE_DIR a worktree of the gh-pages branch; never pushes"
    )

    p_deploy = sub.add_parser("deploy", help="commit the site and push it to GitHub Pages")
    p_deploy.add_argument("--dry-run", action="store_true", help="show what would go live")
    p_deploy.add_argument("--yes", action="store_true", help="don't ask before pushing")
    p_deploy.add_argument("-m", "--message", default="", help="commit message")

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
    # These two only touch files in the site, so they never open the course store.
    if args.command == "unpublish":
        return _cmd_unpublish(get_settings(), course_id=args.course_id)
    if args.command == "site" and args.site_command == "build":
        return _cmd_site_build(get_settings(), out=args.out)
    if args.command == "site" and args.site_command == "init":
        return _cmd_site_init(get_settings())
    if args.command == "deploy":
        return _cmd_deploy(get_settings(), dry_run=args.dry_run, yes=args.yes, message=args.message)
    if args.command == "import":
        return asyncio.run(_with_store(lambda s, st: _cmd_import(s, st, path=args.path)))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
