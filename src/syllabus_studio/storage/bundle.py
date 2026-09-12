"""Portable course bundles — the unit of sharing.

A bundle is one JSON file holding a course outline plus every lesson written
for it.  It is what you hand someone, commit to a repo, or publish to a
community catalog.  Progress is deliberately left out: your completion state
is yours, not part of the course.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from syllabus_studio.core.ids import course_id as new_course_id
from syllabus_studio.core.models import Base, Course, LessonContent, now_ms

FORMAT = "syllabus-studio/course-bundle"
# Not bumped for lenses, faq or provenance: they are additive, and unknown keys
# are ignored, so a new bundle still installs on an old checkout (minus the
# extras). Bumping would make an old `parse` reject it outright.
FORMAT_VERSION = 1


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class Provenance(Base):
    """Who and what produced a bundle's content.

    An open catalog is only as trustworthy as its worst entry, so a bundle says
    which model wrote it and whether a person has read it. The defaults describe
    the honest unknown: no recorded model, not reviewed.
    """

    author_provider: str = ""
    author_model: str = ""
    depth: str = ""
    generated_at: int = 0
    enriched: list[str] = Field(default_factory=list)
    """Authoring passes that ran over every lesson, e.g. ["lenses", "faq"]."""
    human_reviewed: bool = False
    reviewer: str = ""
    note: str = ""


class CourseBundle(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

    format: str = FORMAT
    format_version: int = FORMAT_VERSION
    exported_at: int = Field(default_factory=now_ms)
    exported_by: str = "syllabus-studio"
    license: str = ""
    course: Course
    lessons: dict[str, LessonContent] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)

    # -- build -------------------------------------------------------------

    @staticmethod
    def build(course: Course, lessons: dict[str, LessonContent], *, license: str = "") -> CourseBundle:
        clean = course.model_copy(deep=True)
        clean.progress = {}
        clean.demo = False
        return CourseBundle(course=clean, lessons=lessons, license=license)

    # -- io ----------------------------------------------------------------

    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(by_alias=True, indent=indent)

    def write(self, path: Path | str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @staticmethod
    def parse(data: str | bytes | dict[str, Any]) -> CourseBundle:
        payload = json.loads(data) if isinstance(data, (str, bytes)) else data
        if payload.get("format") not in (FORMAT, None):
            raise ValueError(f"Not a Syllabus Studio bundle: format={payload.get('format')!r}")
        version = int(payload.get("formatVersion") or payload.get("format_version") or 1)
        if version > FORMAT_VERSION:
            raise ValueError(
                f"Bundle format v{version} is newer than this build understands (v{FORMAT_VERSION})."
            )
        return CourseBundle.model_validate(payload)

    @staticmethod
    def read(path: Path | str) -> CourseBundle:
        return CourseBundle.parse(Path(path).read_text(encoding="utf-8"))

    # -- install -----------------------------------------------------------

    def materialise(self, *, origin: str = "imported", keep_id: bool = False) -> Course:
        """Return a fresh Course ready to save, with a new id unless told otherwise."""
        course = self.course.model_copy(deep=True)
        if not keep_id:
            course.id = new_course_id(course.title)
        course.origin = origin
        course.progress = {}
        course.demo = False
        course.created_at = now_ms()
        course.updated_at = now_ms()
        return course


def suggested_filename(course: Course) -> str:
    from syllabus_studio.core.ids import slugify

    return f"{slugify(course.title)}.course.json"
