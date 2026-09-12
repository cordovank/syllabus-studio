"""The storage contract.

Two implementations ship: :mod:`sqlite_store` (a single portable file, no
services) and :mod:`remote_store` (HTTP against another running instance, so
the same courses follow you between machines).  Anything satisfying this
protocol drops in — Postgres, S3, a git-backed folder.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from syllabus_studio.core.models import Course, CourseSummary, LessonContent, LessonProgress


class StorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class CourseStore(Protocol):
    """Every method is async so a remote backend needs no special-casing."""

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def list_courses(self) -> list[CourseSummary]: ...

    async def get_course(self, course_id: str) -> Course | None: ...

    async def save_course(self, course: Course) -> Course: ...

    async def delete_course(self, course_id: str) -> None: ...

    async def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent | None: ...

    async def save_lesson(
        self, course_id: str, lesson_id: str, content: LessonContent
    ) -> LessonContent: ...

    async def list_built_lessons(self, course_id: str) -> list[str]: ...

    async def set_progress(
        self, course_id: str, lesson_id: str, patch: LessonProgress
    ) -> Course: ...

    def describe(self) -> dict[str, object]: ...
