from __future__ import annotations

import pytest

from syllabus_studio.core import build_course, write_lesson
from syllabus_studio.core.builder import BuildError, outline_to_course
from syllabus_studio.core.lessons import LessonError
from syllabus_studio.core.prompts import lesson_prompt, outline_prompt
from syllabus_studio.llm import Message, extract_json


async def test_build_course_from_a_real_syllabus(provider, syllabus: str) -> None:
    course = await build_course(provider, syllabus=syllabus, name="Applied ML", depth="standard")

    assert course.title == "Applied ML"
    assert course.modules, "a course needs modules"
    assert course.source, "the syllabus is kept for per-lesson context"
    ids = [ls.id for _, _, ls in course.all_lessons()]
    assert len(ids) == len(set(ids)), "lesson ids must be unique"
    assert ids[0] == "m1l1", "ids are assigned by us, not the model"


async def test_build_rejects_a_stub(provider) -> None:
    with pytest.raises(BuildError) as err:
        await build_course(provider, syllabus="too short")
    assert err.value.code == "too_short"


def test_outline_ids_are_stable_across_calls(syllabus: str) -> None:
    payload = {
        "title": "X",
        "modules": [
            {"title": "A", "lessons": [{"title": "one"}, {"title": "two"}]},
            {"title": "B", "lessons": [{"title": "three"}]},
        ],
    }
    first = outline_to_course(payload, syllabus=syllabus)
    second = outline_to_course(payload, syllabus=syllabus)
    assert [ls.id for _, _, ls in first.all_lessons()] == ["m1l1", "m1l2", "m2l1"]
    assert [ls.id for _, _, ls in first.all_lessons()] == [
        ls.id for _, _, ls in second.all_lessons()
    ]


def test_outline_clamps_silly_minutes(syllabus: str) -> None:
    payload = {
        "title": "X",
        "modules": [{"title": "A", "lessons": [{"title": "one", "minutes": 900}]}],
    }
    course = outline_to_course(payload, syllabus=syllabus)
    assert course.modules[0].lessons[0].minutes == 25


def test_outline_without_lessons_is_rejected(syllabus: str) -> None:
    with pytest.raises(BuildError):
        outline_to_course({"modules": [{"title": "A", "lessons": []}]}, syllabus=syllabus)


async def test_write_lesson_returns_every_section(provider, syllabus: str) -> None:
    course = await build_course(provider, syllabus=syllabus)
    lid = course.modules[0].lessons[0].id

    content = await write_lesson(provider, course=course, lesson_id=lid)

    assert content.big_idea
    assert content.sections
    assert content.key_terms
    assert content.quiz and 0 <= content.quiz[0].answer < len(content.quiz[0].options)
    assert content.practice
    assert content.provider == "echo"


async def test_write_lesson_for_a_missing_id(provider, syllabus: str) -> None:
    course = await build_course(provider, syllabus=syllabus)
    with pytest.raises(LessonError) as err:
        await write_lesson(provider, course=course, lesson_id="nope")
    assert err.value.code == "not_found"


def test_prompts_carry_their_task_marker(course, syllabus: str) -> None:
    assert "[[SS:OUTLINE]]" in outline_prompt(syllabus=syllabus)
    p = lesson_prompt(
        course=course, module=course.modules[0], module_index=0, lesson=course.modules[0].lessons[0]
    )
    assert "[[SS:LESSON]]" in p
    assert "First lesson" in p
    assert "<-- write this one" in p, "the prompt must point at the lesson being written"


@pytest.mark.parametrize(
    "reply",
    [
        '{"a": 1}',
        'Here you go:\n```json\n{"a": 1}\n```',
        'Sure — {"a": 1} — hope that helps.',
    ],
)
def test_json_extraction_is_tolerant(reply: str) -> None:
    assert extract_json(reply) == {"a": 1}


async def test_streaming_yields_the_same_text_as_complete(provider) -> None:
    msgs = [Message.user("[[SS:LENS]] anything")]
    whole = await provider.complete(msgs)
    streamed = "".join([chunk async for chunk in provider.stream(msgs)])
    assert streamed == whole
