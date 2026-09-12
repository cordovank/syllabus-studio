"""Server-sent events for the streaming tutor endpoints.

Three event types, all carrying JSON:

    event: delta   data: {"text": "..."}    incremental text
    event: done    data: {"text": "..."}    the whole answer, once
    event: error   data: {"code", "message", "partial"}
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from syllabus_studio.llm import LLMError

HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _frame(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _wrap(source: AsyncIterator[str]) -> AsyncIterator[str]:
    whole: list[str] = []
    try:
        async for delta in source:
            whole.append(delta)
            yield _frame("delta", {"text": delta})
    except LLMError as exc:
        yield _frame("error", {**exc.as_dict(), "partial": "".join(whole)})
        return
    except Exception as exc:  # noqa: BLE001
        yield _frame(
            "error",
            {"code": "upstream_error", "message": str(exc), "partial": "".join(whole)},
        )
        return
    yield _frame("done", {"text": "".join(whole)})


def stream_response(source: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(_wrap(source), media_type="text/event-stream", headers=HEADERS)
