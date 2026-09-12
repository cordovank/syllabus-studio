from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse

from syllabus_studio.core.authoring import enrich_course
from syllabus_studio.core.builder import build_course
from syllabus_studio.core.models import Course, CourseSummary, LessonProgress
from syllabus_studio.llm import LLMError
from syllabus_studio.storage import CourseBundle, suggested_filename

from ..deps import AuthorProviderDep, CourseDep, SettingsDep, StoreDep
from ..schemas import BuildCourseRequest, ImportRequest
from ..sse import event_response

router = APIRouter(tags=["courses"])

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample_syllabus.txt"


@router.get("/sample-syllabus", response_class=Response)
async def sample_syllabus() -> Response:
    text = SAMPLE.read_text(encoding="utf-8") if SAMPLE.exists() else ""
    return Response(content=text, media_type="text/plain; charset=utf-8")


@router.get("/courses", response_model=list[CourseSummary])
async def list_courses(store: StoreDep) -> list[CourseSummary]:
    return await store.list_courses()


@router.post("/courses", response_model=Course, status_code=status.HTTP_201_CREATED)
async def create_course(
    body: BuildCourseRequest, provider: AuthorProviderDep, store: StoreDep, settings: SettingsDep
) -> Course:
    course = await build_course(
        provider, syllabus=body.syllabus, name=body.name.strip(), depth=body.depth
    )
    return await store.save_course(course)


@router.post("/courses/{course_id}/enrich")
async def enrich_whole_course(
    course: CourseDep,
    provider: AuthorProviderDep,
    store: StoreDep,
    lenses: bool = True,
    faq: bool = True,
    force: bool = False,
) -> StreamingResponse:
    """Enrich every written lesson. Takes minutes, so it streams ``progress`` frames
    ``{lessonId, stage, done, total}`` and ends on ``done``."""
    # Checked before the stream opens, so "no author model" is an honest 503
    # rather than a 200 whose every lesson fails.
    if not provider.describe().get("available", True):
        raise LLMError("not_configured", "No authoring model is configured.")

    async def events() -> AsyncIterator[tuple[str, dict[str, Any]]]:
        tally: dict[str, int] = {}
        async for progress in enrich_course(
            provider, store, course=course, lenses=lenses, faq=faq, force=force
        ):
            if progress["stage"] != "started":
                tally[progress["stage"]] = tally.get(progress["stage"], 0) + 1
            yield "progress", progress
        yield "done", {"total": len(course.all_lessons()), "outcomes": tally}

    return event_response(events())


@router.get("/courses/{course_id}", response_model=Course)
async def get_course(course: CourseDep) -> Course:
    return course


@router.put("/courses/{course_id}", response_model=Course)
async def put_course(course_id: str, body: Course, store: StoreDep) -> Course:
    """Upsert. Exists so one instance can act as the remote store for another."""
    body.id = course_id
    return await store.save_course(body)


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: str, store: StoreDep) -> Response:
    await store.delete_course(course_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- sharing --------------------------------------------------------------


@router.get("/courses/{course_id}/export")
async def export_course(course: CourseDep, store: StoreDep) -> Response:
    lessons = {}
    for lesson_id in await store.list_built_lessons(course.id):
        content = await store.get_lesson(course.id, lesson_id)
        if content is not None:
            lessons[lesson_id] = content

    bundle = CourseBundle.build(course, lessons)
    return Response(
        content=bundle.to_json(),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{suggested_filename(course)}"'
        },
    )


@router.post("/courses/import", response_model=Course, status_code=status.HTTP_201_CREATED)
async def import_course(body: ImportRequest, store: StoreDep) -> Course:
    bundle = CourseBundle.parse(body.bundle)
    course = bundle.materialise(origin="imported", keep_id=body.keep_id)
    saved = await store.save_course(course)

    for lesson_id, content in bundle.lessons.items():
        if saved.find(lesson_id) is None:
            continue  # the bundle carried a lesson the outline no longer has
        await store.save_lesson(saved.id, lesson_id, content)
        progress = saved.progress.setdefault(lesson_id, LessonProgress())
        progress.built = True

    return await store.save_course(saved)
