from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from syllabus_studio.core.lessons import LessonError
from syllabus_studio.core.tutor import (
    ask,
    available_lenses,
    explain_with_lens,
    precomputed_lens,
    replay,
)
from syllabus_studio.llm import Message

from ..deps import CourseDep, ReaderProviderDep, StoreDep
from ..schemas import AskRequest, LensRequest
from ..sse import stream_response

router = APIRouter(tags=["tutor"])


@router.get("/lenses", response_model=list[dict[str, str]])
async def lenses() -> list[dict[str, str]]:
    return available_lenses()


async def _require_lesson(store: StoreDep, course_id: str, lesson_id: str):  # noqa: ANN202
    content = await store.get_lesson(course_id, lesson_id)
    if content is None:
        raise LessonError(
            "not_found", "Write the lesson first — there is nothing to explain yet."
        )
    return content


@router.post("/courses/{course_id}/lessons/{lesson_id}/lens")
async def lens(
    lesson_id: str,
    body: LensRequest,
    course: CourseDep,
    provider: ReaderProviderDep,
    store: StoreDep,
) -> StreamingResponse:
    content = await _require_lesson(store, course.id, lesson_id)

    # 1. precomputed: no provider touched, not even describe()
    stored = precomputed_lens(content, body.lens)
    if stored is not None:
        return stream_response(replay(stored))

    # 2. live, or 3. not_configured — both decided inside, before the stream opens
    source = explain_with_lens(
        provider, course=course, lesson_id=lesson_id, content=content, lens=body.lens
    )
    return stream_response(source)


@router.post("/courses/{course_id}/lessons/{lesson_id}/ask")
async def ask_question(
    lesson_id: str,
    body: AskRequest,
    course: CourseDep,
    provider: ReaderProviderDep,
    store: StoreDep,
) -> StreamingResponse:
    content = await _require_lesson(store, course.id, lesson_id)
    turns = [Message(role=t.role, content=t.content) for t in body.turns]
    source = ask(provider, course=course, lesson_id=lesson_id, content=content, turns=turns)
    return stream_response(source)
