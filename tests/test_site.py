"""The published reader as static files (spec 003, phase 1)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from syllabus_studio.core.lessons import write_lesson
from syllabus_studio.core.models import Course, LessonProgress
from syllabus_studio.storage import CourseBundle, SQLiteCourseStore
from syllabus_studio.storage.site import (
    READER_FILES,
    WEB_DIR,
    build_site,
    bundle_filename,
    course_id_from_filename,
)


async def _written_course(store: SQLiteCourseStore, course: Course, provider) -> Course:  # noqa: ANN001
    """A course with its first lesson written and some personal progress on it."""
    saved = await store.save_course(course)
    lesson_id = saved.all_lessons()[0][2].id
    content = await write_lesson(provider, course=saved, lesson_id=lesson_id, model_name="test")
    await store.save_lesson(saved.id, lesson_id, content)
    return await store.set_progress(saved.id, lesson_id, LessonProgress(built=True, done=True))


# --- what a build writes --------------------------------------------------


async def test_a_build_writes_the_reader_catalog_and_one_bundle_per_course(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    saved = await _written_course(store, course, provider)

    report = await build_site(store, tmp_path / "site")
    site = tmp_path / "site"

    assert (site / "index.html").is_file()
    assert (site / ".nojekyll").is_file(), "GitHub Pages would run Jekyll without it"
    catalog = json.loads((site / "catalog.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in catalog["entries"]] == [saved.id]
    assert {lens["id"] for lens in catalog["lenses"]} >= {"eli5", "analogy", "picture", "rigor"}

    entry = catalog["entries"][0]
    bundle_path = site / entry["url"]
    assert bundle_path.is_file(), "every catalog url resolves to a file relative to catalog.json"
    assert entry["lessonCount"] == len(saved.all_lessons())
    assert report.courses == [bundle_filename(saved.id)]


async def test_published_bundles_carry_lessons_never_progress(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    saved = await _written_course(store, course, provider)
    await build_site(store, tmp_path)

    bundle = CourseBundle.read(tmp_path / "courses" / bundle_filename(saved.id))
    assert bundle.course.progress == {}
    assert list(bundle.lessons) == [saved.all_lessons()[0][2].id]


async def test_building_again_removes_bundles_of_deleted_courses_but_nothing_it_didnt_write(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    saved = await _written_course(store, course, provider)
    await build_site(store, tmp_path)
    (tmp_path / "CNAME").write_text("courses.example.com\n", encoding="utf-8")

    await store.delete_course(saved.id)
    report = await build_site(store, tmp_path)

    assert not (tmp_path / "courses" / bundle_filename(saved.id)).exists()
    assert report.removed == [bundle_filename(saved.id)]
    assert (tmp_path / "CNAME").read_text(encoding="utf-8") == "courses.example.com\n"
    assert json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))["entries"] == []


async def test_a_storage_failure_leaves_the_existing_site_untouched(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider, monkeypatch
) -> None:
    await _written_course(store, course, provider)
    await build_site(store, tmp_path)
    before = (tmp_path / "catalog.json").read_bytes()

    async def broken(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("disk gone")

    monkeypatch.setattr(store, "list_built_lessons", broken)
    with pytest.raises(RuntimeError):
        await build_site(store, tmp_path)
    assert (tmp_path / "catalog.json").read_bytes() == before


# --- the reader's files -----------------------------------------------------

# Anything that would resolve against the host root instead of the site's own
# folder, and so break on a sub-path host like https://<owner>.github.io/<repo>/.
ROOT_RELATIVE = re.compile(
    r"""(?:href|src|action)\s*=\s*["']/(?!/)"""  # html attributes
    r"""|(?:from|import)\s*\(?\s*["']/(?!/)"""  # js imports
    r"""|fetch\(\s*[`"']/(?!/)"""  # js fetches
    r"""|url\(\s*["']?/(?!/)""",  # css
)


def _reader_sources() -> dict[str, str]:
    return {dest: (WEB_DIR / source).read_text(encoding="utf-8") for source, dest in READER_FILES}


def test_the_reader_has_no_root_relative_urls() -> None:
    for dest, text in _reader_sources().items():
        match = ROOT_RELATIVE.search(text)
        assert match is None, f"{dest} has a root-relative URL: {match.group(0) if match else ''}"


def test_every_module_the_reader_imports_is_shipped() -> None:
    shipped = {dest for _, dest in READER_FILES}
    for dest, text in _reader_sources().items():
        if not dest.endswith(".js"):
            continue
        for spec in re.findall(r"""from\s+["'](\.[^"']+)["']""", text):
            target = (Path(dest).parent / spec).as_posix()
            target = str(Path(target))  # normalise ./
            assert target in shipped, f"{dest} imports {spec}, which the site build doesn't copy"

    html = _reader_sources()["index.html"]
    for ref in re.findall(r"""(?:src|href)="(static/[^"]+)\"""", html):
        assert ref in shipped, f"index.html references {ref}, which the site build doesn't copy"


def test_the_reader_never_reaches_the_server_api() -> None:
    # The layering rule: the Studio talks to /api/v1 through api.js; the reader
    # reads files through data.js. If this fails, the static site is broken.
    for dest, text in _reader_sources().items():
        code = re.sub(r"/\*.*?\*/|(?<![:\"'])//[^\n]*|<!--.*?-->", "", text, flags=re.S)
        assert "api.js" not in code, dest
        assert "/api/v1" not in code, dest


def test_bundle_file_names_are_checked_before_they_touch_anything() -> None:
    assert course_id_from_filename("applied-ml-62f7f9.course.json") == "applied-ml-62f7f9"
    for bad in ("../secrets.course.json", "x.json", ".course.json", "a/b.course.json"):
        assert course_id_from_filename(bad) is None, bad


# --- the server's preview of the same site ----------------------------------


def test_the_server_serves_the_reader_from_the_same_generators(client, syllabus: str) -> None:
    course = client.post("/api/v1/courses", json={"syllabus": syllabus}).json()
    cid = course["id"]
    lid = course["modules"][0]["lessons"][0]["id"]
    client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate")

    res = client.get("/reader", follow_redirects=False)
    assert res.is_redirect
    assert res.headers["location"] == "/reader/", "the reader's relative paths need the slash"

    catalog = client.get("/reader/catalog.json").json()
    entry = next(e for e in catalog["entries"] if e["id"] == cid)

    bundle = client.get(f"/reader/{entry['url']}").json()
    assert bundle["course"]["id"] == cid
    assert lid in bundle["lessons"]
    assert bundle["course"]["progress"] == {}

    for _, dest in READER_FILES:
        assert client.get(f"/reader/{dest}").status_code == 200, dest


def test_the_preview_serves_only_what_a_build_ships(client) -> None:
    # studio.js and api.js exist under /static for the Studio, but a reader that
    # imported them would break once published — so the preview must 404 too.
    for path in ("static/js/studio.js", "static/js/api.js", "static/js/library.js", "studio.html"):
        assert client.get(f"/reader/{path}").status_code == 404, path
    assert client.get("/reader/courses/..%2Fsecrets.course.json").status_code == 404
    assert client.get("/reader/courses/missing.course.json").status_code == 404
