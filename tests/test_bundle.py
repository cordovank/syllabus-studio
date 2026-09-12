from __future__ import annotations

import pytest

from syllabus_studio.core.models import Course, LessonContent, LessonProgress
from syllabus_studio.storage import CourseBundle


def test_bundle_drops_progress(course: Course) -> None:
    course.progress["m1l1"] = LessonProgress(done=True, score=3, total=3)
    bundle = CourseBundle.build(course, {"m1l1": LessonContent(big_idea="x")})

    assert bundle.course.progress == {}, "completion state is personal, not part of the course"
    assert "m1l1" in bundle.lessons


def test_round_trip_through_json(course: Course) -> None:
    bundle = CourseBundle.build(course, {"m1l1": LessonContent(big_idea="idea")})
    again = CourseBundle.parse(bundle.to_json())

    assert again.course.title == course.title
    assert again.lessons["m1l1"].big_idea == "idea"
    assert len(again.course.modules[0].lessons) == 2


def test_materialise_gives_a_new_id(course: Course) -> None:
    bundle = CourseBundle.build(course, {})
    fresh = bundle.materialise()

    assert fresh.id != course.id
    assert fresh.origin == "imported"
    assert fresh.progress == {}

    kept = bundle.materialise(keep_id=True)
    assert kept.id == course.id


def test_rejects_a_foreign_document() -> None:
    with pytest.raises(ValueError, match="Not a Syllabus Studio bundle"):
        CourseBundle.parse('{"format": "something/else", "course": {}}')


def test_rejects_a_newer_format(course: Course) -> None:
    payload = CourseBundle.build(course, {}).model_dump(by_alias=True)
    payload["formatVersion"] = 99
    with pytest.raises(ValueError, match="newer than this build"):
        CourseBundle.parse(payload)


def test_the_shipped_demo_bundle_parses() -> None:
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "syllabus_studio" / "data" / "demo_course.json"
    )
    bundle = CourseBundle.read(path)
    assert bundle.course.modules, "the sample course must have modules"
    assert bundle.lessons, "the sample course ships with one lesson already written"
    written = next(iter(bundle.lessons.values()))
    assert written.quiz and written.key_terms and written.worked.steps
