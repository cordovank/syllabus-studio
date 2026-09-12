"""Syllabus -> course outline."""

from __future__ import annotations

from typing import Any

from syllabus_studio.llm import BaseProvider, LLMError, Message

from .ids import course_id as new_course_id
from .ids import lesson_id as new_lesson_id
from .ids import module_id as new_module_id
from .models import Course, Depth, Lesson, Module, now_ms
from .prompts import outline_prompt

MAX_MODULES = 8
MAX_LESSONS_PER_MODULE = 6
MIN_SYLLABUS_CHARS = 60
MAX_STORED_SOURCE = 12_000


class BuildError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _clamp_minutes(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 12
    return max(6, min(25, n))


def outline_to_course(
    payload: dict[str, Any], *, syllabus: str, name: str = "", depth: Depth = "standard"
) -> Course:
    """Turn the model's JSON into a validated Course with stable ids.

    Ids are assigned here, never by the model — that keeps them predictable and
    keeps progress keys stable across a rewrite.
    """
    raw_modules = payload.get("modules") or []
    if not raw_modules:
        raise BuildError("invalid_json", "The model returned no modules.")

    modules: list[Module] = []
    for mi, rm in enumerate(raw_modules[:MAX_MODULES]):
        lessons = [
            Lesson(
                id=new_lesson_id(mi, li),
                title=str(rl.get("title") or f"Lesson {li + 1}").strip(),
                hook=str(rl.get("hook") or "").strip(),
                minutes=_clamp_minutes(rl.get("minutes")),
            )
            for li, rl in enumerate((rm.get("lessons") or [])[:MAX_LESSONS_PER_MODULE])
        ]
        if not lessons:
            continue
        modules.append(
            Module(
                id=new_module_id(mi),
                title=str(rm.get("title") or f"Module {mi + 1}").strip(),
                summary=str(rm.get("summary") or "").strip(),
                outcomes=[str(o).strip() for o in (rm.get("outcomes") or [])][:5],
                lessons=lessons,
            )
        )

    if not modules:
        raise BuildError("invalid_json", "The model returned modules with no lessons.")

    title = name or str(payload.get("title") or "Untitled course").strip()
    level = str(payload.get("level") or "intermediate").lower()
    if level not in ("beginner", "intermediate", "advanced"):
        level = "intermediate"

    return Course(
        id=new_course_id(title),
        title=title,
        subtitle=str(payload.get("subtitle") or "").strip(),
        level=level,  # type: ignore[arg-type]
        depth=depth,
        skills=[str(s).strip() for s in (payload.get("skills") or [])][:6],
        source=syllabus[:MAX_STORED_SOURCE],
        modules=modules,
        created_at=now_ms(),
        updated_at=now_ms(),
    )


async def build_course(
    provider: BaseProvider, *, syllabus: str, name: str = "", depth: Depth = "standard"
) -> Course:
    syllabus = syllabus.strip()
    if len(syllabus) < MIN_SYLLABUS_CHARS:
        raise BuildError(
            "too_short",
            "Paste at least the course description and the topic list — a few lines won't "
            "produce a useful course.",
        )

    prompt = outline_prompt(syllabus=syllabus, name=name, depth=depth)
    try:
        payload = await provider.complete_json([Message.user(prompt)], tier="default")
    except LLMError as exc:
        raise BuildError(exc.code, exc.message) from exc

    if not isinstance(payload, dict):
        raise BuildError("invalid_json", "The model did not return a JSON object.")
    return outline_to_course(payload, syllabus=syllabus, name=name, depth=depth)
