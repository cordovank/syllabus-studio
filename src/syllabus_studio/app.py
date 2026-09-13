"""FastAPI application factory.

Layering, so the frontend stays replaceable:

    web/  ->  /api/v1  ->  core/  ->  llm/ + storage/

``web/`` is mounted as plain static files and talks to the app only through the
JSON API.  Swapping it for a React or HTMX build means changing this one mount
and nothing else.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from syllabus_studio import __version__
from syllabus_studio.api import errors
from syllabus_studio.api.routes import api_router
from syllabus_studio.config import Settings, get_settings
from syllabus_studio.core.models import LessonProgress
from syllabus_studio.llm import get_provider
from syllabus_studio.storage import CourseBundle, CourseStore, get_store
from syllabus_studio.storage.site import (
    READER_FILES,
    course_id_from_filename,
    site_bundle,
    site_catalog,
)

log = logging.getLogger("syllabus_studio")

WEB_DIR = Path(__file__).parent / "web"
DEMO_BUNDLE = Path(__file__).parent / "data" / "demo_course.json"

# There is no build step, so asset URLs never change between versions. Without an
# explicit policy browsers cache ES modules heuristically and keep running old JS
# against a new API — which is how the capability chip once read "none (read only)"
# on a working author model. "no-cache" still uses the cache, but revalidates via
# the ETag first, so an unchanged file costs a 304.
NO_CACHE = {"Cache-Control": "no-cache"}


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(NO_CACHE)
        return response


async def seed_demo_course(store: CourseStore) -> None:
    """Install the bundled sample course the first time the app runs.

    It is a normal course, not a special case — it arrives through the same
    bundle import path as anything from the community catalog.
    """
    if not DEMO_BUNDLE.exists():
        return
    if await store.list_courses():
        return

    bundle = CourseBundle.parse(json.loads(DEMO_BUNDLE.read_text(encoding="utf-8")))
    course = bundle.materialise(origin="bundled", keep_id=True)
    course.demo = True
    await store.save_course(course)

    for lesson_id, content in bundle.lessons.items():
        if course.find(lesson_id) is None:
            continue
        await store.save_lesson(course.id, lesson_id, content)
        course.progress.setdefault(lesson_id, LessonProgress()).built = True

    await store.save_course(course)
    log.info("Seeded the bundled sample course (%s).", course.id)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = get_store(settings)
        await store.startup()
        app.state.settings = settings
        app.state.store = store
        app.state.providers = {
            "author": get_provider(settings, role="author"),
            "reader": get_provider(settings, role="reader"),
        }
        if settings.seed_demo_course:
            try:
                await seed_demo_course(store)
            except Exception:  # noqa: BLE001  (a bad seed must never block boot)
                log.exception("Could not seed the sample course.")
        try:
            yield
        finally:
            await store.shutdown()

    app = FastAPI(
        title="Syllabus Studio",
        version=__version__,
        summary="Turn a syllabus into an interactive, self-paced course.",
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    errors.install(app)
    app.include_router(api_router)

    if WEB_DIR.exists():
        app.mount(
            "/static", RevalidatingStaticFiles(directory=WEB_DIR / "static"), name="static"
        )

        # This server is the author's tool. Readers get the published static reader
        # (spec 003) and never run it, so its front door is the Studio.
        @app.get("/", include_in_schema=False)
        async def index() -> RedirectResponse:
            return RedirectResponse("/studio")

        @app.get("/studio", include_in_schema=False)
        async def studio() -> FileResponse:
            return FileResponse(WEB_DIR / "studio.html", headers=NO_CACHE)

        # The published reader, served here as the author's preview. It reads the same
        # catalog.json and bundles a site build writes, generated from the store on
        # each request, and only the files READER_FILES lists — so a file the build
        # would leave out is missing here too, not just on the live site.
        reader_files = {dest: WEB_DIR / source for source, dest in READER_FILES}

        @app.get("/reader", include_in_schema=False)
        async def reader_root() -> RedirectResponse:
            # The trailing slash is load-bearing: the reader's paths are relative.
            return RedirectResponse("/reader/")

        @app.get("/reader/catalog.json", include_in_schema=False)
        async def reader_catalog(request: Request) -> JSONResponse:
            return JSONResponse(await site_catalog(request.app.state.store), headers=NO_CACHE)

        @app.get("/reader/courses/{name}", include_in_schema=False)
        async def reader_bundle(name: str, request: Request) -> Response:
            course_id = course_id_from_filename(name)
            bundle = await site_bundle(request.app.state.store, course_id) if course_id else None
            if bundle is None:
                return Response(status_code=404)
            return Response(bundle.to_json(), media_type="application/json", headers=NO_CACHE)

        @app.get("/reader/{path:path}", include_in_schema=False)
        async def reader_file(path: str) -> Response:
            source = reader_files.get(path or "index.html")
            if source is None:
                return Response(status_code=404)
            return FileResponse(source, headers=NO_CACHE)

    return app
