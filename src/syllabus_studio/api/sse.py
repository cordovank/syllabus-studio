"""Server-sent events for the streaming endpoints.

Every frame carries JSON. The tutor endpoints stream text:

    event: delta   data: {"text": "..."}    incremental text
    event: done    data: {"text": "..."}    the whole answer, once
    event: error   data: {"code", "message", "partial"}

Long-running jobs (bulk enrichment) stream named events of their own through
:func:`event_response`, ending on ``done`` or ``error``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

from syllabus_studio.llm import LLMError

HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

Event = tuple[str, dict[str, Any]]


def _frame(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_payload(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if isinstance(exc, LLMError) or (isinstance(code, str) and isinstance(message, str)):
        return {"code": code, "message": message}
    return {"code": "upstream_error", "message": str(exc)}


async def _frames(source: AsyncIterator[Event]) -> AsyncIterator[str]:
    # By the time the source raises, the 200 has been sent, so a failure can
    # only be reported in-band.
    try:
        async for event, payload in source:
            yield _frame(event, payload)
    except Exception as exc:  # noqa: BLE001
        yield _frame("error", _error_payload(exc))


def event_response(source: AsyncIterator[Event]) -> StreamingResponse:
    return StreamingResponse(_frames(source), media_type="text/event-stream", headers=HEADERS)


async def _text_events(source: AsyncIterator[str]) -> AsyncIterator[Event]:
    whole: list[str] = []
    try:
        async for delta in source:
            whole.append(delta)
            yield "delta", {"text": delta}
    except Exception as exc:  # noqa: BLE001  (caught here, not in _frames, to keep ``partial``)
        payload = exc.as_dict() if isinstance(exc, LLMError) else _error_payload(exc)
        yield "error", {**payload, "partial": "".join(whole)}
        return
    yield "done", {"text": "".join(whole)}


def stream_response(source: AsyncIterator[str]) -> StreamingResponse:
    return event_response(_text_events(source))
