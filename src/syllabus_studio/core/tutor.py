"""The tutor: re-explanation lenses and the per-lesson question thread.

Both stream, so both return async iterators of text deltas.

A lens is resolved in order: the text precomputed at authoring time, then the
reader's live model, then ``not_configured``. The first step never touches a
provider — that is what lets a reader with no model still use every lens a
bundle shipped with.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from syllabus_studio.llm import BaseProvider, LLMError, Message

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


def precomputed_lens(content: LessonContent, lens: str) -> str | None:
    """The lens text written at authoring time, if this lesson shipped with it."""
    text = content.lenses.get(lens, "")
    return text if text.strip() else None


async def replay(text: str) -> AsyncIterator[str]:
    """Stored text as a one-delta stream, so the client can't tell it from a live one."""
    yield text


def _require_live(provider: BaseProvider, feature: str) -> None:
    # Raised here, synchronously, so the route can still answer 503 before the
    # stream opens. ``describe()`` is the cheap static answer; a provider that is
    # configured but unreachable (a stopped daemon) still fails in-band.
    if not provider.describe().get("available", False):
        raise LLMError(
            "not_configured",
            f"{feature} needs a model, and no reader model is configured. "
            "Set SS_READER_PROVIDER (or SS_LLM_PROVIDER).",
        )


def explain_with_lens(
    provider: BaseProvider, *, course: Course, lesson_id: str, content: LessonContent, lens: str
) -> AsyncIterator[str]:
    if lens not in LENSES:
        raise TutorError("unknown_lens", f"No lens {lens!r}. Try: {', '.join(LENSES)}.")
    found = course.find(lesson_id)
    if found is None:
        raise TutorError("not_found", f"No lesson {lesson_id!r} in this course.")
    _, _, lesson = found
    _require_live(provider, "This lens was not precomputed, so it")

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

    _require_live(provider, "Asking about a lesson")

    trimmed = list(turns)[-MAX_THREAD_TURNS:]
    if trimmed[0].role != "user":
        trimmed = trimmed[1:]

    standing = Message.user(tutor_system_turn(course=course, lesson=lesson, content=content))
    return provider.stream([standing, *trimmed], tier="default")
