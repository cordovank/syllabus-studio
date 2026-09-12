"""Lesson writing — the per-lesson generation step."""

from __future__ import annotations

from typing import Any

from syllabus_studio.llm import BaseProvider, LLMError, Message

from .models import Course, LessonContent, now_ms
from .prompts import lesson_prompt


class LessonError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _coerce(payload: dict[str, Any]) -> LessonContent:
    """Accept the model's JSON leniently; drop anything malformed rather than failing."""
    sections = [s for s in (payload.get("sections") or []) if isinstance(s, dict)]
    if not sections:
        raise LessonError("invalid_json", "The model returned a lesson with no sections.")

    quiz = []
    for q in payload.get("quiz") or []:
        if not isinstance(q, dict):
            continue
        options = [str(o) for o in (q.get("options") or [])]
        if len(options) < 2:
            continue
        try:
            answer = int(q.get("answer", 0))
        except (TypeError, ValueError):
            answer = 0
        quiz.append(
            {
                "q": str(q.get("q") or ""),
                "options": options,
                "answer": max(0, min(len(options) - 1, answer)),
                "why": str(q.get("why") or ""),
            }
        )

    return LessonContent.model_validate(
        {
            "bigIdea": str(payload.get("bigIdea") or payload.get("big_idea") or ""),
            "sections": [
                {
                    "heading": str(s.get("heading") or ""),
                    "body": str(s.get("body") or ""),
                    "note": str(s.get("note") or ""),
                }
                for s in sections
            ],
            "keyTerms": [
                {"term": str(t.get("term") or ""), "definition": str(t.get("definition") or "")}
                for t in (payload.get("keyTerms") or payload.get("key_terms") or [])
                if isinstance(t, dict) and t.get("term")
            ],
            "worked": {
                "title": str((payload.get("worked") or {}).get("title") or "Worked example"),
                "steps": [str(s) for s in ((payload.get("worked") or {}).get("steps") or [])],
            },
            "quiz": quiz,
            "practice": [
                {"task": str(p.get("task") or ""), "hint": str(p.get("hint") or "")}
                for p in (payload.get("practice") or [])
                if isinstance(p, dict) and p.get("task")
            ],
            "generatedAt": now_ms(),
        }
    )


async def write_lesson(
    provider: BaseProvider, *, course: Course, lesson_id: str, model_name: str = ""
) -> LessonContent:
    found = course.find(lesson_id)
    if found is None:
        raise LessonError("not_found", f"No lesson {lesson_id!r} in this course.")
    module_index, module, lesson = found

    prompt = lesson_prompt(
        course=course, module=module, module_index=module_index, lesson=lesson
    )
    try:
        payload = await provider.complete_json([Message.user(prompt)], tier="default")
    except LLMError as exc:
        raise LessonError(exc.code, exc.message) from exc

    if not isinstance(payload, dict):
        raise LessonError("invalid_json", "The model did not return a JSON object.")

    content = _coerce(payload)
    content.provider = provider.name
    content.model = model_name
    return content
