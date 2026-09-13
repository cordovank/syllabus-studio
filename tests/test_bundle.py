from __future__ import annotations

import json

import pytest

from syllabus_studio.core.models import Course, FaqItem, LessonContent, LessonProgress
from syllabus_studio.storage import CatalogEntry, CourseBundle, Provenance


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


# --- precomputed tutoring and provenance (spec 001, stage 2) ---------------


def _enriched() -> LessonContent:
    return LessonContent(
        big_idea="idea",
        lenses={"eli5": "Like you're five.", "rigor": "Formally."},
        faq=[FaqItem(q="Why not X?", a="Because Y.")],
    )


def test_lenses_and_faq_survive_a_round_trip(course: Course) -> None:
    bundle = CourseBundle.build(course, {"m1l1": _enriched()})
    again = CourseBundle.parse(bundle.to_json()).lessons["m1l1"]

    assert again.lenses == {"eli5": "Like you're five.", "rigor": "Formally."}
    assert again.faq == [FaqItem(q="Why not X?", a="Because Y.")]


def test_a_bundle_with_neither_new_field_still_parses(course: Course) -> None:
    payload = CourseBundle.build(course, {"m1l1": _enriched()}).model_dump(by_alias=True)
    for lesson in payload["lessons"].values():
        del lesson["lenses"], lesson["faq"]
    del payload["provenance"]

    bundle = CourseBundle.parse(payload)

    lesson = bundle.lessons["m1l1"]
    assert lesson.big_idea == "idea"
    assert lesson.lenses == {} and lesson.faq == []
    assert bundle.provenance == Provenance()


def test_the_shipped_v1_demo_bundle_reads_with_empty_extras() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src/syllabus_studio/data/demo_course.json"
    bundle = CourseBundle.read(path)
    assert all(not c.lenses and not c.faq for c in bundle.lessons.values())


def test_unknown_keys_are_ignored_so_newer_bundles_install_on_older_code(course: Course) -> None:
    # What lets a new bundle install on an old checkout: the old models simply
    # drop fields they don't know. Pinned here, since forbidding extras anywhere
    # in the tree would quietly break it.
    payload = CourseBundle.build(course, {"m1l1": _enriched()}).model_dump(by_alias=True)
    payload["somethingFromTheFuture"] = {"x": 1}
    payload["lessons"]["m1l1"]["alsoFromTheFuture"] = [1, 2]
    payload["provenance"]["futureField"] = True

    bundle = CourseBundle.parse(payload)
    assert bundle.lessons["m1l1"].big_idea == "idea"


def test_the_new_fields_do_not_bump_the_format_version(course: Course) -> None:
    payload = CourseBundle.build(course, {"m1l1": _enriched()}).model_dump(by_alias=True)
    assert payload["formatVersion"] == 1


def test_provenance_defaults_are_sane_and_unreviewed_unless_set(course: Course) -> None:
    bundle = CourseBundle.build(course, {})
    p = bundle.provenance

    assert p.human_reviewed is False
    assert p.reviewer == "" and p.author_model == "" and p.enriched == []
    assert p.generated_at == 0

    reviewed = Provenance(human_reviewed=True, reviewer="Ada", enriched=["lenses", "faq"])
    bundle.provenance = reviewed
    again = CourseBundle.parse(bundle.to_json())
    assert again.provenance.human_reviewed is True
    assert again.provenance.reviewer == "Ada"


def test_the_new_fields_are_camel_case_on_the_wire(course: Course) -> None:
    bundle = CourseBundle.build(course, {"m1l1": _enriched()})
    payload = json.loads(bundle.to_json())

    assert {"humanReviewed", "authorModel", "authorProvider", "generatedAt"} <= set(
        payload["provenance"]
    )
    entry = CatalogEntry(id="x", title="X", author_model="m", human_reviewed=True, lesson_count=3)
    wire = entry.model_dump(by_alias=True)
    assert {"authorModel", "humanReviewed", "lessonCount"} <= set(wire)
    assert "author_model" not in wire


def test_a_catalog_entry_without_provenance_mirrors_still_loads() -> None:
    entry = CatalogEntry.model_validate({"id": "applied-ml", "title": "Applied ML"})
    assert entry.human_reviewed is False
    assert entry.lesson_count is None
    assert entry.enriched == []


# --- provenance survives import and re-export (spec 002 §4) ------------------


def test_provenance_survives_import_then_export_unchanged(course: Course) -> None:
    published = CourseBundle.build(course, {"m1l1": _enriched()})
    published.provenance = Provenance(
        author_provider="anthropic", author_model="m", human_reviewed=True, reviewer="Ada"
    )

    installed = CourseBundle.parse(published.to_json()).materialise()
    reexported = CourseBundle.build(installed, {"m1l1": _enriched()})

    assert reexported.provenance == published.provenance, "only publishing stamps provenance"
    assert reexported.course.provenance is None, "it travels once, at the top of the bundle"


def test_a_course_stored_before_provenance_existed_still_loads_and_exports(course: Course) -> None:
    row = course.model_dump(by_alias=True)
    del row["provenance"]  # what an older database row looks like

    loaded = Course.model_validate(row)
    assert loaded.provenance is None
    assert CourseBundle.build(loaded, {}).provenance == Provenance()


def test_published_at_is_additive(course: Course) -> None:
    payload = CourseBundle.build(course, {}).model_dump(by_alias=True)
    assert payload["publishedAt"] == 0 and payload["formatVersion"] == 1
    del payload["publishedAt"]
    assert CourseBundle.parse(payload).published_at == 0
