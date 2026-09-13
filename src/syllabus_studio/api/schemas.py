"""Request and response bodies — the contract the web client codes against.

Responses reuse the domain models from :mod:`syllabus_studio.core.models`;
only the request shapes live here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from syllabus_studio.core.models import Course, Depth


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class Base(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class BuildCourseRequest(Base):
    syllabus: str = Field(min_length=1)
    name: str = ""
    depth: Depth = "standard"


class ProgressPatch(Base):
    done: bool | None = None
    built: bool | None = None
    score: int | None = None
    total: int | None = None


class LensRequest(Base):
    lens: str


class Turn(Base):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(Base):
    turns: list[Turn] = Field(min_length=1)


class ImportRequest(Base):
    bundle: dict[str, Any]
    keep_id: bool = False


class PublishRequest(Base):
    reviewed_by: str = ""
    """Who read it. Empty publishes it marked *not reviewed*, and the catalog says so."""
    force: bool = False
    """Redo enrichment that already exists. Never rewrites a lesson."""


class Capabilities(Base):
    """What this install can do, each answered by the role that serves it."""

    author_courses: bool
    """Author role: build courses, write and enrich lessons."""
    live_tutor: bool
    """Reader role: the ask box."""
    live_lenses: bool
    """Reader role: lenses that were not precomputed. Precomputed ones need nothing."""


class HealthResponse(Base):
    status: str = "ok"
    version: str
    llm: dict[str, Any]
    """``{"author": {...}, "reader": {...}}`` — each ``describe()`` merged with ``probe()``."""
    storage: dict[str, Any]
    lenses: list[dict[str, str]]
    capabilities: Capabilities


class ErrorBody(Base):
    code: str
    message: str


class CourseResponse(Base):
    course: Course
    built_lessons: list[str] = Field(default_factory=list)
