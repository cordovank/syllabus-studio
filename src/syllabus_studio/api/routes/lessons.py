from __future__ import annotations

from fastapi import APIRouter, status

from syllabus_studio.core.lessons import LessonError, write_lesson
from syllabus_studio.core.models import Course, LessonContent, LessonProgress

from ..deps import CourseDep, ProviderDep, SettingsDep, StoreDep
from ..schemas import ProgressPatch

router = APIRouter(tags=["lessons"])


@router.get("/courses/{course_id}/lessons", response_model=list[str])
async def list_built_lessons(course_id: str, store: StoreDep) -> list[str]:
    """Ids of lessons that have already been written."""
    return await store.list_built_lessons(course_id)


@router.get("/courses/{course_id}/lessons/{lesson_id}", response_model=LessonContent)
async def get_lesson(course_id: str, lesson_id: str, store: StoreDep) -> LessonContent:
    content = await store.get_lesson(course_id, lesson_id)
    if content is None:
        raise LessonError("not_found", "This lesson has not been written yet.")
    return content


@router.put("/courses/{course_id}/lessons/{lesson_id}", response_model=LessonContent)
async def put_lesson(
    course_id: str, lesson_id: str, body: LessonContent, store: StoreDep
) -> LessonContent:
    """Upsert — used by the remote store, and handy for hand-editing a lesson."""
    return await store.save_lesson(course_id, lesson_id, body)


@router.post(
    "/courses/{course_id}/lessons/{lesson_id}/generate",
    response_model=LessonContent,
    status_code=status.HTTP_201_CREATED,
)
async def generate_lesson(
    lesson_id: str,
    course: CourseDep,
    provider: ProviderDep,
    store: StoreDep,
    settings: SettingsDep,
    force: bool = False,
) -> LessonContent:
    """Write the lesson, or return the stored one unless ``force`` is set."""
    if not force:
        existing = await store.get_lesson(course.id, lesson_id)
        if existing is not None:
            return existing

    content = await write_lesson(
        provider,
        course=course,
        lesson_id=lesson_id,
        model_name=settings.model_for_tier("default"),
    )
    await store.save_lesson(course.id, lesson_id, content)
    await store.set_progress(course.id, lesson_id, LessonProgress(built=True))
    return content


# There is deliberately no lesson delete: rewriting overwrites in place, which
# keeps progress keys stable. "Clear and redo" is generate with force=true.


@router.put("/courses/{course_id}/lessons/{lesson_id}/progress", response_model=Course)
async def set_progress(
    course_id: str, lesson_id: str, body: ProgressPatch, store: StoreDep
) -> Course:
    patch = LessonProgress.model_validate(body.model_dump(exclude_none=True, by_alias=True))
    return await store.set_progress(course_id, lesson_id, patch)
