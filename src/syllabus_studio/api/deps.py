"""Request-scoped dependencies, resolved off ``app.state``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from syllabus_studio.config import Settings
from syllabus_studio.core.models import Course
from syllabus_studio.llm import BaseProvider
from syllabus_studio.storage import CourseStore, StorageError


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> CourseStore:
    return request.app.state.store


def get_provider(request: Request) -> BaseProvider:
    return request.app.state.provider


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
StoreDep = Annotated[CourseStore, Depends(get_store)]
ProviderDep = Annotated[BaseProvider, Depends(get_provider)]


async def load_course(course_id: str, store: StoreDep) -> Course:
    course = await store.get_course(course_id)
    if course is None:
        raise StorageError("not_found", f"No course {course_id!r}.")
    return course


CourseDep = Annotated[Course, Depends(load_course)]
