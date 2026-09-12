from __future__ import annotations

import pytest

from syllabus_studio.core.models import Course, LessonContent, LessonProgress, Section
from syllabus_studio.storage.base import StorageError


async def test_save_and_read_back(store, course: Course) -> None:
    await store.save_course(course)
    again = await store.get_course("c1")
    assert again is not None
    assert again.title == "Test Course"
    assert [m.id for m in again.modules] == ["m1"]
    assert again.updated_at > 0


async def test_list_is_newest_first(store, course: Course) -> None:
    await store.save_course(course)
    second = course.model_copy(deep=True)
    second.id, second.title = "c2", "Later Course"
    await store.save_course(second)

    rows = await store.list_courses()
    assert [r.id for r in rows] == ["c2", "c1"]
    assert rows[0].lesson_count == 2


async def test_progress_merges_rather_than_replaces(store, course: Course) -> None:
    await store.save_course(course)

    await store.set_progress("c1", "m1l1", LessonProgress(built=True))
    await store.set_progress("c1", "m1l1", LessonProgress(done=True, score=2, total=3))

    again = await store.get_course("c1")
    p = again.progress["m1l1"]
    assert p.built is True, "the earlier 'built' flag must survive the second patch"
    assert p.done is True and p.score == 2 and p.total == 3


async def test_progress_on_missing_course_raises(store) -> None:
    with pytest.raises(StorageError) as err:
        await store.set_progress("nope", "m1l1", LessonProgress(done=True))
    assert err.value.code == "not_found"


async def test_lessons_round_trip(store, course: Course) -> None:
    await store.save_course(course)
    content = LessonContent(big_idea="Idea", sections=[Section(heading="H", body="B")])

    await store.save_lesson("c1", "m1l1", content)
    assert await store.list_built_lessons("c1") == ["m1l1"]

    got = await store.get_lesson("c1", "m1l1")
    assert got is not None and got.big_idea == "Idea"
    assert got.sections[0].heading == "H"

    assert await store.get_lesson("c1", "m1l2") is None


async def test_delete_removes_lessons_too(store, course: Course) -> None:
    await store.save_course(course)
    await store.save_lesson("c1", "m1l1", LessonContent(big_idea="x"))

    await store.delete_course("c1")

    assert await store.get_course("c1") is None
    assert await store.list_built_lessons("c1") == []


async def test_camel_case_is_the_wire_format(course: Course) -> None:
    content = LessonContent(big_idea="x")
    payload = content.model_dump(by_alias=True)
    assert "bigIdea" in payload and "keyTerms" in payload
    assert "big_idea" not in payload


async def test_a_lesson_row_stored_before_lenses_and_faq_reads_them_as_empty(
    store, course: Course
) -> None:
    await store.save_course(course)
    old_body = '{"bigIdea": "written long ago", "sections": [], "generatedAt": 1}'
    await store._write(
        "INSERT INTO lessons (course_id, lesson_id, generated_at, body) VALUES (?, ?, ?, ?)",
        ("c1", "m1l1", 1, old_body),
    )

    content = await store.get_lesson("c1", "m1l1")

    assert content is not None and content.big_idea == "written long ago"
    assert content.lenses == {} and content.faq == []
