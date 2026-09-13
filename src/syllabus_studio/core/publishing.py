"""The model half of publishing: every lesson written, every lesson enriched.

Publishing is two halves with different owners. This one spends tokens and
knows nothing about files; ``storage.site`` stamps the bundle and writes the
site, and ``syllabus_studio.publishing`` runs the two in order for the CLI and
the API alike, so they can't drift. Kept apart because ``core`` must never learn
about a storage engine or a filesystem.

Events are plain dicts, streamed as they happen — publishing a course takes
minutes:

    {"phase": "write",  "stage": "writing" | "written", "lessonId", "done", "total"}
    {"phase": "enrich", ...}            each event of ``authoring.enrich_course``
    {"phase": "ready"}                  every lesson that could be written is

A lesson that fails to write raises :class:`PublishError` after its event. The
course is not publishable with a hole in it; what was written is kept, so a rerun
only writes what's still missing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from syllabus_studio.llm import BaseProvider

from .authoring import enrich_course
from .lessons import LessonError, write_lesson
from .models import Course, LessonProgress


class PublishError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def prepare_course(
    provider: BaseProvider,
    store: Any,
    *,
    course: Course,
    model_name: str,
    force: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Write missing lessons, then enrich all of them.

    ``store`` is a ``CourseStore``; typed loosely to keep core free of storage imports.
    ``force`` redoes enrichment only — it never rewrites a lesson, since a rewrite
    is a deliberate, per-lesson choice made in the Studio.
    """
    built = set(await store.list_built_lessons(course.id))
    missing = [ls for _, _, ls in course.all_lessons() if ls.id not in built]

    for done, lesson in enumerate(missing):
        base = {"phase": "write", "lessonId": lesson.id, "total": len(missing)}
        yield {**base, "stage": "writing", "done": done}
        try:
            content = await write_lesson(
                provider, course=course, lesson_id=lesson.id, model_name=model_name
            )
        except LessonError as exc:
            raise PublishError(
                exc.code,
                f"Couldn't write {lesson.id} ({lesson.title}): {exc.message} "
                "Nothing was published; lessons already written are kept.",
            ) from exc
        await store.save_lesson(course.id, lesson.id, content)
        await store.set_progress(course.id, lesson.id, LessonProgress(built=True))
        yield {**base, "stage": "written", "done": done + 1}

    async for event in enrich_course(
        provider, store, course=course, lenses=True, faq=True, force=force
    ):
        yield {"phase": "enrich", **event}

    yield {"phase": "ready"}
