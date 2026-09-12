"""The tutor: re-explanation lenses and the per-lesson question thread.

Both stream, so both return async iterators of text deltas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from syllabus_studio.llm import BaseProvider, Message

from .models import Course, LessonContent
from .prompts import LENSES, lens_prompt, tutor_system_turn

MAX_THREAD_TURNS = 12


class TutorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def available_lenses() -> list[dict[str, str]]:
    return [{"id": k, "label": v["label"]} for k, v in LENSES.items()]


def explain_with_lens(
    provider: BaseProvider, *, course: Course, lesson_id: str, content: LessonContent, lens: str
) -> AsyncIterator[str]:
    if lens not in LENSES:
        raise TutorError("unknown_lens", f"No lens {lens!r}. Try: {', '.join(LENSES)}.")
    found = course.find(lesson_id)
    if found is None:
        raise TutorError("not_found", f"No lesson {lesson_id!r} in this course.")
    _, _, lesson = found

    prompt = lens_prompt(course=course, lesson=lesson, content=content, lens=lens)
    return provider.stream([Message.user(prompt)], tier="default")


def ask(
    provider: BaseProvider,
    *,
    course: Course,
    lesson_id: str,
    content: LessonContent,
    turns: Sequence[Message],
) -> AsyncIterator[str]:
    """``turns`` is the page's own thread, oldest first, ending on a user turn."""
    found = course.find(lesson_id)
    if found is None:
        raise TutorError("not_found", f"No lesson {lesson_id!r} in this course.")
    _, _, lesson = found

    if not turns or turns[-1].role != "user":
        raise TutorError("invalid_thread", "The conversation must end with a user turn.")

    trimmed = list(turns)[-MAX_THREAD_TURNS:]
    if trimmed[0].role != "user":
        trimmed = trimmed[1:]

    standing = Message.user(tutor_system_turn(course=course, lesson=lesson, content=content))
    return provider.stream([standing, *trimmed], tier="default")
