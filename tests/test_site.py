"""The published reader as static files: publishing into the site, and serving it
(spec 003, phases 1-3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from syllabus_studio.core.lessons import LessonError
from syllabus_studio.core.models import Course, LessonProgress
from syllabus_studio.publishing import PublishError, publish_course
from syllabus_studio.storage import CourseBundle, SQLiteCourseStore
from syllabus_studio.storage.site import (
    READER_FILES,
    WEB_DIR,
    bundle_filename,
    course_id_from_filename,
    publish_report,
    read_site_catalog,
    unpublish,
    write_reader_files,
)

MODEL = "test-model"


async def _publish(store, provider, course: Course, site: Path, **kw) -> dict:  # noqa: ANN001
    saved = await store.save_course(course)
    events = [
        e
        async for e in publish_course(
            provider, store, course=saved, site_dir=site, model_name=MODEL, **kw
        )
    ]
    kind, report = events[-1]
    assert kind == "published"
    assert all(k == "progress" for k, _ in events[:-1])
    return report


def _catalog(site: Path) -> dict:
    return json.loads((site / "catalog.json").read_text(encoding="utf-8"))


# --- publishing into the site ----------------------------------------------------


async def test_publishing_writes_every_lesson_enriches_and_puts_the_course_in_the_site(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    report = await _publish(store, provider, course, tmp_path)

    bundle = CourseBundle.read(tmp_path / "courses" / bundle_filename(course.id))
    assert set(bundle.lessons) == {ls.id for _, _, ls in course.all_lessons()}, "none left out"
    p = bundle.provenance
    assert (p.author_provider, p.author_model) == ("echo", MODEL)
    assert p.enriched == ["lenses", "faq"] and p.human_reviewed is False
    assert bundle.published_at > 0

    (entry,) = _catalog(tmp_path)["entries"]
    assert entry["id"] == course.id
    assert entry["authorModel"] == p.author_model, "catalog mirrors equal the bundle"
    assert entry["humanReviewed"] is p.human_reviewed
    assert entry["enriched"] == p.enriched
    assert entry["publishedAt"] == bundle.published_at
    assert (tmp_path / entry["url"]).is_file()

    assert (tmp_path / "index.html").is_file() and (tmp_path / ".nojekyll").is_file()
    assert report["unwritten"] == report["missingLenses"] == report["missingFaq"] == []

    stored = await store.get_course(course.id)
    assert stored.provenance == p, "the course keeps what it was published with"


async def test_publishing_twice_leaves_one_entry_and_keeps_what_the_author_set_by_hand(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    await _publish(store, provider, course, tmp_path)
    catalog = _catalog(tmp_path)
    by_hand = {"description": "Hand-written", "tags": ["ml"], "license": "CC BY 4.0"}
    catalog["entries"][0] |= by_hand
    (tmp_path / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")

    await _publish(store, provider, course, tmp_path, reviewer="Ada")

    (entry,) = _catalog(tmp_path)["entries"]
    assert {k: entry[k] for k in by_hand} == by_hand
    assert entry["humanReviewed"] is True, "republishing updated the rest"


async def test_published_bundles_carry_lessons_never_progress(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    course.progress = {"m1l1": LessonProgress(done=True, score=3, total=3)}
    await _publish(store, provider, course, tmp_path)

    bundle = CourseBundle.read(tmp_path / "courses" / bundle_filename(course.id))
    assert bundle.course.progress == {}
    assert bundle.course.provenance is None, "provenance travels once, at the top"


async def test_a_lesson_that_fails_to_write_publishes_nothing(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider, monkeypatch
) -> None:
    other = course.model_copy(deep=True, update={"id": "other", "title": "Other"})
    await _publish(store, provider, other, tmp_path)
    before = (tmp_path / "catalog.json").read_bytes()

    async def refuse(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise LessonError("refused", "no")

    monkeypatch.setattr("syllabus_studio.core.publishing.write_lesson", refuse)
    with pytest.raises(PublishError) as err:
        await _publish(store, provider, course, tmp_path)

    assert err.value.code == "refused"
    assert (tmp_path / "catalog.json").read_bytes() == before, "catalog.json byte-identical"
    assert not (tmp_path / "courses" / bundle_filename(course.id)).exists()


async def test_unpublishing_removes_the_entry_and_bundle_and_nothing_else(
    tmp_path: Path, store: SQLiteCourseStore, course: Course, provider
) -> None:
    other = course.model_copy(deep=True, update={"id": "other", "title": "Other"})
    await _publish(store, provider, other, tmp_path)
    await _publish(store, provider, course, tmp_path)
    (tmp_path / "CNAME").write_text("courses.example.com\n", encoding="utf-8")

    assert unpublish(tmp_path, course.id) is True

    assert [e["id"] for e in _catalog(tmp_path)["entries"]] == ["other"]
    assert not (tmp_path / "courses" / bundle_filename(course.id)).exists()
    assert (tmp_path / "courses" / bundle_filename("other")).exists()
    assert (tmp_path / "CNAME").read_text(encoding="utf-8") == "courses.example.com\n"
    assert unpublish(tmp_path, course.id) is False, "nothing left to remove"
    assert unpublish(tmp_path, "../escape") is False


def test_site_build_refreshes_the_reader_and_publishes_nothing(tmp_path: Path) -> None:
    write_reader_files(tmp_path)
    assert (tmp_path / "index.html").is_file() and (tmp_path / ".nojekyll").is_file()
    assert _catalog(tmp_path)["entries"] == []

    catalog = {
        "name": "Mine",
        "entries": [{"id": "x", "title": "X", "url": "courses/x.course.json"}],
    }
    (tmp_path / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    (tmp_path / "CNAME").write_text("c.example.com", encoding="utf-8")
    write_reader_files(tmp_path)

    assert json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8")) == catalog
    assert (tmp_path / "CNAME").exists()


def test_the_report_names_what_a_reader_would_find_missing(course: Course) -> None:
    from syllabus_studio.core.models import LessonContent

    bundle = CourseBundle.build(course, {"m1l1": LessonContent(big_idea="x")})
    report = publish_report(bundle, {"url": "courses/c1.course.json"}, Path("site"))

    assert report["unwritten"] == ["m1l2"]
    assert report["missingLenses"] == ["m1l1"] and report["missingFaq"] == ["m1l1"]
    assert report["humanReviewed"] is False


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


# --- the author's server: publish, unpublish, the site on disk, previews ----------


def _sse(text: str) -> list[tuple[str, dict]]:
    frames = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((fields["event"], json.loads(fields["data"])))
    return frames


def _course(client, syllabus: str, name: str) -> str:
    return client.post("/api/v1/courses", json={"syllabus": syllabus, "name": name}).json()["id"]


def _publish_via_api(client, cid: str, **body) -> dict:  # noqa: ANN003
    res = client.post(f"/api/v1/courses/{cid}/publish", json=body)
    assert res.status_code == 200, res.text
    frames = _sse(res.text)
    event, report = frames[-1]
    assert event == "done", frames[-1]
    assert {e for e, _ in frames[:-1]} <= {"progress"}
    return report


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_publishing_through_the_api_streams_progress_then_the_report(
    client, settings, syllabus: str
) -> None:
    cid = _course(client, syllabus, "Applied ML")
    report = _publish_via_api(client, cid, reviewedBy="Ada")

    assert report["courseId"] == cid and report["humanReviewed"] is True
    assert report["reviewer"] == "Ada"
    assert (settings.site_dir / report["bundle"]).is_file()

    status = client.get("/api/v1/site").json()
    assert [c["id"] for c in status["courses"]] == [cid]
    assert status["courses"][0]["publishedAt"] == report["publishedAt"]

    exported = json.loads(client.get(f"/api/v1/courses/{cid}/export").text)
    assert exported["provenance"]["reviewer"] == "Ada", "an export says what was published"


def test_publishing_needs_an_author_model_before_the_stream_opens(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from syllabus_studio.app import create_app
    from syllabus_studio.config import Settings
    from syllabus_studio.storage.bundle import CourseBundle as Bundle

    settings = Settings(
        _env_file=None,
        llm_provider="none",
        author_provider="",
        reader_provider="",
        db_path=tmp_path / "t.db",
        site_dir=tmp_path / "site",
        seed_demo_course=False,
    )
    with TestClient(create_app(settings)) as client:
        demo = Path(__file__).resolve().parents[1] / "src/syllabus_studio/data/demo_course.json"
        bundle = Bundle.read(demo).model_dump(by_alias=True)
        cid = client.post("/api/v1/courses/import", json={"bundle": bundle}).json()["id"]

        res = client.post(f"/api/v1/courses/{cid}/publish", json={})
        assert res.status_code == 503
        assert res.json()["code"] == "not_configured"
        assert not (tmp_path / "site").exists(), "nothing was written"


def test_unpublishing_through_the_api(client, settings, syllabus: str) -> None:
    cid = _course(client, syllabus, "Applied ML")
    _publish_via_api(client, cid)

    assert client.delete(f"/api/v1/courses/{cid}/publish").status_code == 204
    assert client.get("/api/v1/site").json()["courses"] == []
    assert client.delete(f"/api/v1/courses/{cid}/publish").status_code == 404


def test_the_reader_root_serves_the_site_as_it_is_on_disk(client, settings, syllabus: str) -> None:
    res = client.get("/reader", follow_redirects=False)
    assert res.is_redirect
    assert res.headers["location"] == "/reader/", "the reader's relative paths need the slash"

    # nothing published yet: an empty catalog, not every draft in the store
    cid = _course(client, syllabus, "Drafted")
    assert client.get("/reader/catalog.json").json()["entries"] == []

    _publish_via_api(client, cid)
    entry = client.get("/reader/catalog.json").json()["entries"][0]
    served = client.get(f"/reader/{entry['url']}")
    assert served.status_code == 200
    assert served.content == (settings.site_dir / entry["url"]).read_bytes()

    for _, dest in READER_FILES:
        assert client.get(f"/reader/{dest}").status_code == 200, dest


def test_a_preview_is_the_site_with_one_course_upserted_and_nothing_written(
    client, settings, syllabus: str
) -> None:
    published = _course(client, syllabus, "Published")
    _publish_via_api(client, published)
    draft = _course(client, syllabus, "Draft")
    lid = client.get(f"/api/v1/courses/{draft}").json()["modules"][0]["lessons"][0]["id"]
    assert client.post(f"/api/v1/courses/{draft}/lessons/{lid}/generate").status_code == 201
    before = _snapshot(settings.site_dir)

    base = f"/reader/preview/{draft}/"
    catalog = client.get(base + "catalog.json").json()
    ids = [e["id"] for e in catalog["entries"]]
    assert ids[0] == draft, "a course not yet on the site comes first"
    assert published in ids, "the rest of the site is still there"

    entry = catalog["entries"][0]
    assert entry["preview"] is True
    assert entry["humanReviewed"] is False
    assert entry["authorModel"], "stamped from the current author settings"
    assert "publishedAt" not in entry, "a preview isn't published"
    assert not any(e.get("preview") for e in catalog["entries"][1:])

    live = client.get(base + entry["url"]).json()
    assert live["course"]["id"] == draft and lid in live["lessons"]
    assert live["provenance"]["authorProvider"] == "echo"

    other = next(e for e in catalog["entries"] if e["id"] == published)
    assert (
        client.get(base + other["url"]).content == (settings.site_dir / other["url"]).read_bytes()
    ), "other courses are the site's copies, byte for byte"

    assert client.get(base).status_code == 200
    assert client.get(base + "static/js/reader.js").status_code == 200
    assert _snapshot(settings.site_dir) == before, "a preview never writes the site"


def test_a_redirect_opens_the_previewed_course(client, syllabus: str) -> None:
    cid = _course(client, syllabus, "Draft")
    res = client.get(f"/reader/preview/{cid}", follow_redirects=False)
    assert res.headers["location"] == f"/reader/preview/{cid}/#/course/{cid}"


def test_the_reader_serves_only_what_a_build_ships(client) -> None:
    # studio.js and api.js exist under /static for the Studio, but a reader that
    # imported them would break once published — so the server must 404 too.
    for base in ("/reader/", "/reader/preview/some-course/"):
        for path in (
            "static/js/studio.js",
            "static/js/api.js",
            "static/js/library.js",
            "studio.html",
        ):
            assert client.get(base + path).status_code == 404, base + path
    assert client.get("/reader/courses/..%2Fsecrets.course.json").status_code == 404
    assert client.get("/reader/courses/missing.course.json").status_code == 404
    assert client.get("/reader/preview/missing/catalog.json").status_code == 404
    assert client.get("/reader/preview/..%2F..%2Fetc/catalog.json").status_code == 404


def test_a_catalog_on_disk_that_is_broken_reads_as_empty(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text("{not json", encoding="utf-8")
    assert read_site_catalog(tmp_path)["entries"] == []
    assert read_site_catalog(tmp_path / "missing")["entries"] == []


def test_provenance_names_the_model_that_actually_wrote_the_course(tmp_path: Path) -> None:
    from syllabus_studio.config import Settings
    from syllabus_studio.llm import get_provider
    from syllabus_studio.publishing import author_model_name

    base = {"author_provider": "", "reader_provider": "", "db_path": tmp_path / "t.db"}
    echo = get_provider(Settings(_env_file=None, llm_provider="echo", **base))
    assert author_model_name(echo) == "echo", "never the generic Claude default"

    ollama = Settings(_env_file=None, llm_provider="ollama", **base)
    assert author_model_name(get_provider(ollama)) == ollama.model_for_tier("default", "ollama")
    none = get_provider(Settings(_env_file=None, llm_provider="none", **base))
    assert author_model_name(none) == ""
