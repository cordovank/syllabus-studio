"""Shared fixtures. Every test runs on the offline provider and a throwaway DB."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from syllabus_studio.config import Settings
from syllabus_studio.core.models import Course, Lesson, Module
from syllabus_studio.llm import get_provider
from syllabus_studio.storage import SQLiteCourseStore

SAMPLE = Path(__file__).resolve().parents[1] / "src" / "syllabus_studio" / "data" / "sample_syllabus.txt"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # .env is loaded into the environment, so pin everything that decides whether
    # a test leaves the machine: the suite runs offline whatever .env says.
    return Settings(
        llm_provider="echo",
        author_provider="",
        reader_provider="",
        storage_backend="sqlite",
        db_path=tmp_path / "test.db",
        site_dir=tmp_path / "site",
        seed_demo_course=False,
        catalog_url="",
    )


@pytest.fixture
def provider(settings: Settings):
    return get_provider(settings)


@pytest.fixture
async def store(settings: Settings) -> AsyncIterator[SQLiteCourseStore]:
    s = SQLiteCourseStore(settings.db_path)
    await s.startup()
    yield s
    await s.shutdown()


@pytest.fixture
def syllabus() -> str:
    return SAMPLE.read_text(encoding="utf-8")


@pytest.fixture
def course() -> Course:
    return Course(
        id="c1",
        title="Test Course",
        modules=[
            Module(
                id="m1",
                title="First module",
                lessons=[
                    Lesson(id="m1l1", title="First lesson", minutes=10),
                    Lesson(id="m1l2", title="Second lesson", minutes=12),
                ],
            )
        ],
    )


@pytest.fixture
def client(settings: Settings) -> Iterator:
    """A TestClient with the app wired to the throwaway settings."""
    from fastapi.testclient import TestClient

    from syllabus_studio.app import create_app

    with TestClient(create_app(settings)) as c:
        yield c
