"""SQLite-backed store: one portable file, no services to run.

Blocking sqlite3 calls are pushed to a worker thread so the event loop keeps
serving while a write lands.  A single lock serialises writers, which is all a
single-user desktop app needs.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from syllabus_studio.core.models import (
    Course,
    CourseSummary,
    LessonContent,
    LessonProgress,
    now_ms,
)

from .base import StorageError

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class SQLiteCourseStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    async def startup(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        def _open() -> sqlite3.Connection:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA)
            conn.commit()
            return conn

        self._conn = await asyncio.to_thread(_open)

    async def shutdown(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("not_ready", "The store has not been started.")
        return self._conn

    async def _read(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(lambda: self.conn.execute(sql, args).fetchall())

    async def _write(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        async with self._lock:

            def _go() -> None:
                self.conn.execute(sql, args)
                self.conn.commit()

            await asyncio.to_thread(_go)

    # -- courses -----------------------------------------------------------

    async def list_courses(self) -> list[CourseSummary]:
        rows = await self._read(
            "SELECT body FROM courses ORDER BY updated_at DESC, rowid DESC"
        )
        return [CourseSummary.of(Course.model_validate_json(r["body"])) for r in rows]

    async def get_course(self, course_id: str) -> Course | None:
        rows = await self._read("SELECT body FROM courses WHERE id = ?", (course_id,))
        return Course.model_validate_json(rows[0]["body"]) if rows else None

    async def save_course(self, course: Course) -> Course:
        course.updated_at = now_ms()
        await self._write(
            """
            INSERT INTO courses (id, title, demo, origin, created_at, updated_at, body)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                demo = excluded.demo,
                origin = excluded.origin,
                updated_at = excluded.updated_at,
                body = excluded.body
            """,
            (
                course.id,
                course.title,
                int(course.demo),
                course.origin,
                course.created_at,
                course.updated_at,
                course.model_dump_json(by_alias=True),
            ),
        )
        return course

    async def delete_course(self, course_id: str) -> None:
        await self._write("DELETE FROM lessons WHERE course_id = ?", (course_id,))
        await self._write("DELETE FROM courses WHERE id = ?", (course_id,))

    # -- lessons -----------------------------------------------------------

    async def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent | None:
        rows = await self._read(
            "SELECT body FROM lessons WHERE course_id = ? AND lesson_id = ?",
            (course_id, lesson_id),
        )
        return LessonContent.model_validate_json(rows[0]["body"]) if rows else None

    async def save_lesson(
        self, course_id: str, lesson_id: str, content: LessonContent
    ) -> LessonContent:
        await self._write(
            """
            INSERT INTO lessons (course_id, lesson_id, generated_at, body)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(course_id, lesson_id) DO UPDATE SET
                generated_at = excluded.generated_at,
                body = excluded.body
            """,
            (course_id, lesson_id, content.generated_at, content.model_dump_json(by_alias=True)),
        )
        return content

    async def list_built_lessons(self, course_id: str) -> list[str]:
        rows = await self._read(
            "SELECT lesson_id FROM lessons WHERE course_id = ?", (course_id,)
        )
        return [r["lesson_id"] for r in rows]

    # -- progress ----------------------------------------------------------

    async def set_progress(
        self, course_id: str, lesson_id: str, patch: LessonProgress
    ) -> Course:
        course = await self.get_course(course_id)
        if course is None:
            raise StorageError("not_found", f"No course {course_id!r}.")
        current = course.progress.get(lesson_id, LessonProgress())
        merged = current.model_copy(
            update=patch.model_dump(exclude_unset=True, by_alias=False)
        )
        course.progress[lesson_id] = merged
        return await self.save_course(course)

    def describe(self) -> dict[str, object]:
        return {"backend": "sqlite", "path": str(self.db_path), "portable": True}
