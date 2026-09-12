"""Domain models.

These are the single source of truth for the API contract: they serialise to
camelCase, which is what the web client consumes.  Work in snake_case in
Python; FastAPI emits aliases on the way out.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Depth = Literal["new", "standard", "deep"]
Level = Literal["beginner", "intermediate", "advanced"]


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


def now_ms() -> int:
    return int(time.time() * 1000)


class Base(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


# --------------------------------------------------------------------------
# course outline
# --------------------------------------------------------------------------


class Lesson(Base):
    id: str
    title: str
    hook: str = ""
    minutes: int = 12


class Module(Base):
    id: str
    title: str
    summary: str = ""
    outcomes: list[str] = Field(default_factory=list)
    lessons: list[Lesson] = Field(default_factory=list)


class LessonProgress(Base):
    done: bool = False
    built: bool = False
    score: int | None = None
    total: int | None = None


class Course(Base):
    id: str
    title: str
    subtitle: str = ""
    level: Level = "intermediate"
    depth: Depth = "standard"
    skills: list[str] = Field(default_factory=list)
    source: str = ""
    """The syllabus this was built from, truncated. Used as context per lesson."""
    modules: list[Module] = Field(default_factory=list)
    progress: dict[str, LessonProgress] = Field(default_factory=dict)
    demo: bool = False
    origin: str = "local"
    """local | imported | catalog:<entry-id> — where this course came from."""
    created_at: int = Field(default_factory=now_ms)
    updated_at: int = Field(default_factory=now_ms)

    def all_lessons(self) -> list[tuple[int, Module, Lesson]]:
        return [(mi, m, ls) for mi, m in enumerate(self.modules) for ls in m.lessons]

    def find(self, lesson_id: str) -> tuple[int, Module, Lesson] | None:
        for mi, m, ls in self.all_lessons():
            if ls.id == lesson_id:
                return mi, m, ls
        return None

    def stats(self) -> tuple[int, int]:
        """(completed, total)."""
        total = len(self.all_lessons())
        done = sum(1 for p in self.progress.values() if p.done)
        return done, total


class CourseSummary(Base):
    id: str
    title: str
    updated_at: int
    lesson_count: int = 0
    done_count: int = 0
    demo: bool = False

    @staticmethod
    def of(course: Course) -> CourseSummary:
        done, total = course.stats()
        return CourseSummary(
            id=course.id,
            title=course.title,
            updated_at=course.updated_at,
            lesson_count=total,
            done_count=done,
            demo=course.demo,
        )


# --------------------------------------------------------------------------
# lesson content
# --------------------------------------------------------------------------


class Section(Base):
    heading: str = ""
    body: str = ""
    note: str = ""


class KeyTerm(Base):
    term: str
    definition: str


class Worked(Base):
    title: str = "Worked example"
    steps: list[str] = Field(default_factory=list)


class QuizItem(Base):
    q: str
    options: list[str] = Field(default_factory=list)
    answer: int = 0
    why: str = ""


class PracticeItem(Base):
    task: str
    hint: str = ""


class FaqItem(Base):
    q: str
    a: str


class LessonContent(Base):
    big_idea: str = ""
    sections: list[Section] = Field(default_factory=list)
    key_terms: list[KeyTerm] = Field(default_factory=list)
    worked: Worked = Field(default_factory=Worked)
    quiz: list[QuizItem] = Field(default_factory=list)
    practice: list[PracticeItem] = Field(default_factory=list)
    generated_at: int = Field(default_factory=now_ms)
    provider: str = ""
    model: str = ""
    # Both default empty so every existing row, bundle and test reads unchanged.
    lenses: dict[str, str] = Field(default_factory=dict)
    """lens id -> the full re-explanation, written at authoring time."""
    faq: list[FaqItem] = Field(default_factory=list)
    """Likely questions about this lesson, answered at authoring time."""
