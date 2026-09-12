"""One error shape for the whole API: ``{"code": ..., "message": ...}``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from syllabus_studio.core.authoring import AuthoringError
from syllabus_studio.core.builder import BuildError
from syllabus_studio.core.lessons import LessonError
from syllabus_studio.core.tutor import TutorError
from syllabus_studio.llm import LLMError
from syllabus_studio.storage import CatalogError, StorageError

STATUS: dict[str, int] = {
    "not_found": 404,
    "too_short": 422,
    "invalid_request": 400,
    "invalid_thread": 400,
    "unknown_lens": 400,
    "not_configured": 503,
    "sampling_disabled": 503,
    "unavailable": 503,
    "not_ready": 503,
    "rate_limited": 429,
    "prompt_too_large": 413,
    "refused": 422,
    "invalid_json": 502,
    "empty_completion": 502,
    "upstream_error": 502,
}


def _payload(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=STATUS.get(code, 500), content={"code": code, "message": message})


def install(app: FastAPI) -> None:
    @app.exception_handler(LLMError)
    async def _llm(_: Request, exc: LLMError) -> JSONResponse:
        return _payload(exc.code, exc.message)

    @app.exception_handler(BuildError)
    async def _build(_: Request, exc: BuildError) -> JSONResponse:
        return _payload(exc.code, exc.message)

    @app.exception_handler(LessonError)
    async def _lesson(_: Request, exc: LessonError) -> JSONResponse:
        return _payload(exc.code, exc.message)

    @app.exception_handler(AuthoringError)
    async def _authoring(_: Request, exc: AuthoringError) -> JSONResponse:
        return _payload(exc.code, exc.message)

    @app.exception_handler(TutorError)
    async def _tutor(_: Request, exc: TutorError) -> JSONResponse:
        return _payload(exc.code, exc.message)

    @app.exception_handler(StorageError)
    async def _storage(_: Request, exc: StorageError) -> JSONResponse:
        return _payload(exc.code, exc.message)

    @app.exception_handler(CatalogError)
    async def _catalog(_: Request, exc: CatalogError) -> JSONResponse:
        return _payload("upstream_error", str(exc))
