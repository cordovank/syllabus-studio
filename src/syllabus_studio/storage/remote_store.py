"""HTTP store — courses live on another running instance of this app.

Set ``SS_STORAGE_BACKEND=remote`` and ``SS_REMOTE_URL=https://host`` to read and
write against a shared server, so the same courses follow you between machines.
The remote speaks exactly the API in ``api/routes`` — meaning any instance can
act as the server for any other.
"""

from __future__ import annotations

from typing import Any

import httpx

from syllabus_studio.core.models import Course, CourseSummary, LessonContent, LessonProgress

from .base import StorageError


class RemoteCourseStore:
    def __init__(self, base_url: str, token: str = "", *, timeout: float = 30.0) -> None:
        if not base_url:
            raise StorageError("not_configured", "SS_REMOTE_URL is required for the remote store.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        headers = {"authorization": f"Bearer {self.token}"} if self.token else {}
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1", timeout=self.timeout, headers=headers
        )

    async def shutdown(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise StorageError("not_ready", "The remote store has not been started.")
        return self._client

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = await self.client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise StorageError("unavailable", f"Could not reach {self.base_url}: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise StorageError("upstream_error", f"{resp.status_code} from remote: {resp.text[:200]}")
        return resp.json() if resp.content else None

    async def list_courses(self) -> list[CourseSummary]:
        data = await self._json("GET", "/courses") or []
        return [CourseSummary.model_validate(d) for d in data]

    async def get_course(self, course_id: str) -> Course | None:
        data = await self._json("GET", f"/courses/{course_id}")
        return Course.model_validate(data) if data else None

    async def save_course(self, course: Course) -> Course:
        data = await self._json(
            "PUT", f"/courses/{course.id}", json=course.model_dump(by_alias=True)
        )
        return Course.model_validate(data) if data else course

    async def delete_course(self, course_id: str) -> None:
        await self._json("DELETE", f"/courses/{course_id}")

    async def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent | None:
        data = await self._json("GET", f"/courses/{course_id}/lessons/{lesson_id}")
        return LessonContent.model_validate(data) if data else None

    async def save_lesson(
        self, course_id: str, lesson_id: str, content: LessonContent
    ) -> LessonContent:
        await self._json(
            "PUT",
            f"/courses/{course_id}/lessons/{lesson_id}",
            json=content.model_dump(by_alias=True),
        )
        return content

    async def list_built_lessons(self, course_id: str) -> list[str]:
        course = await self.get_course(course_id)
        if course is None:
            return []
        return [lid for lid, p in course.progress.items() if p.built]

    async def set_progress(self, course_id: str, lesson_id: str, patch: LessonProgress) -> Course:
        data = await self._json(
            "PUT",
            f"/courses/{course_id}/lessons/{lesson_id}/progress",
            json=patch.model_dump(by_alias=True, exclude_unset=True),
        )
        if data is None:
            raise StorageError("not_found", f"No course {course_id!r} on the remote.")
        return Course.model_validate(data)

    def describe(self) -> dict[str, object]:
        return {"backend": "remote", "url": self.base_url, "portable": False}
