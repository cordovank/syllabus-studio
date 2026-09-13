"""Authoring passes: lenses and FAQ written once, on the author's model."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from syllabus_studio.config import Settings
from syllabus_studio.core import build_course, write_lesson
from syllabus_studio.core.authoring import (
    AuthoringError,
    enrich_lesson,
    enriched_passes,
    generate_faq,
    precompute_lenses,
)
from syllabus_studio.core.models import Course, FaqItem, LessonContent
from syllabus_studio.core.prompts import LENSES
from syllabus_studio.llm import LLMError, Message, ModelTier
from syllabus_studio.llm.providers.echo_provider import EchoProvider


@pytest.fixture(autouse=True)
def instant_echo(monkeypatch) -> None:
    """Echo's simulated latency adds seconds here (publish alone is ~70 calls) and tests
    nothing. CountingEcho keeps its own sleep, which the concurrency test needs."""

    async def complete(self, messages, *, tier="default", max_tokens=None):  # noqa: ANN001, ANN202
        return self._reply(messages)

    monkeypatch.setattr(EchoProvider, "complete", complete)


class CountingEcho(EchoProvider):
    """Echo, but counts calls, can fail chosen lenses, and records peak concurrency."""

    def __init__(self, settings: Settings, *, fail_lenses: Sequence[str] = ()) -> None:
        super().__init__(settings)
        self.calls = 0
        self.fail_lenses = set(fail_lenses)
        self.in_flight = 0
        self.peak = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> str:
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            prompt = messages[-1].content
            for lens in self.fail_lenses:
                if LENSES[lens]["ask"] in prompt:
                    raise LLMError("upstream_error", f"simulated timeout on {lens}")
            return self._reply(messages)
        finally:
            self.in_flight -= 1


@pytest.fixture
async def written(provider, syllabus: str) -> tuple[Course, str, LessonContent]:
    course = await build_course(provider, syllabus=syllabus, name="Applied ML")
    lesson_id = course.modules[0].lessons[0].id
    content = await write_lesson(provider, course=course, lesson_id=lesson_id)
    return course, lesson_id, content


# --- lenses ---------------------------------------------------------------


async def test_every_lens_is_precomputed_and_ids_match_the_prompt_table(provider, written) -> None:
    course, lid, content = written
    lenses = await precompute_lenses(provider, course=course, lesson_id=lid, content=content)

    assert set(lenses) == set(LENSES)
    assert all(text.strip() for text in lenses.values())


async def test_one_failing_lens_is_skipped_and_the_others_survive(settings, written) -> None:
    course, lid, content = written
    flaky = CountingEcho(settings, fail_lenses=["picture"])

    lenses = await precompute_lenses(flaky, course=course, lesson_id=lid, content=content)

    assert set(lenses) == set(LENSES) - {"picture"}


async def test_lenses_raise_only_when_every_one_fails(settings, written) -> None:
    course, lid, content = written
    dead = CountingEcho(settings, fail_lenses=list(LENSES))

    with pytest.raises(AuthoringError) as err:
        await precompute_lenses(dead, course=course, lesson_id=lid, content=content)
    assert err.value.code == "upstream_error"


async def test_no_more_than_two_lenses_are_generated_at_once(settings, written) -> None:
    course, lid, content = written
    counting = CountingEcho(settings)

    await precompute_lenses(counting, course=course, lesson_id=lid, content=content)

    assert counting.calls == len(LENSES)
    assert counting.peak == 2


# --- faq ------------------------------------------------------------------


async def test_faq_parses_to_items_with_a_question_and_an_answer(provider, written) -> None:
    course, lid, content = written
    faq = await generate_faq(provider, course=course, lesson_id=lid, content=content)

    assert faq and all(isinstance(item, FaqItem) for item in faq)
    assert all(item.q.strip() and item.a.strip() for item in faq)


def test_faq_prompt_asks_for_an_object_because_json_mode_cannot_emit_an_array(written) -> None:
    # Ollama's format:"json" forces a top-level object; asked for an array, gpt-oss
    # returned only the first item and every FAQ in a publish failed.
    from syllabus_studio.core.prompts import faq_prompt

    course, lid, content = written
    lesson = course.find(lid)[2]
    reply_shape = faq_prompt(course=course, lesson=lesson, content=content).rsplit("\n", 1)[-1]
    assert reply_shape.startswith("{")


async def test_faq_still_accepts_a_bare_array(settings, written) -> None:
    class Bare(EchoProvider):
        def _reply(self, messages: Sequence[Message]) -> str:
            return json.dumps(json.loads(super()._reply(messages))["faq"])

    course, lid, content = written
    faq = await generate_faq(Bare(settings), course=course, lesson_id=lid, content=content)
    assert len(faq) == 3


async def test_faq_salvages_a_single_bare_row_rather_than_failing(settings, written) -> None:
    class OneRow(EchoProvider):
        def _reply(self, messages: Sequence[Message]) -> str:
            return json.dumps({"q": "Why split by customer?", "a": "Rows share a customer."})

    course, lid, content = written
    faq = await generate_faq(OneRow(settings), course=course, lesson_id=lid, content=content)
    assert faq == [FaqItem(q="Why split by customer?", a="Rows share a customer.")]


# --- enrich_lesson --------------------------------------------------------


async def test_enrich_leaves_the_lesson_itself_byte_identical(provider, written) -> None:
    course, lid, content = written
    before = content.model_dump_json(include={"sections", "quiz", "practice", "generated_at"})

    enriched = await enrich_lesson(provider, course=course, lesson_id=lid, content=content)

    after = enriched.model_dump_json(include={"sections", "quiz", "practice", "generated_at"})
    assert after == before
    assert enriched.lenses and enriched.faq
    assert content.lenses == {} and content.faq == [], "returns a copy, never mutates"


async def test_enriching_twice_is_idempotent_and_spends_nothing_the_second_time(
    settings, written
) -> None:
    course, lid, content = written
    counting = CountingEcho(settings)

    once = await enrich_lesson(counting, course=course, lesson_id=lid, content=content)
    calls_after_first = counting.calls
    twice = await enrich_lesson(counting, course=course, lesson_id=lid, content=once)

    assert twice == once
    assert counting.calls == calls_after_first, "without force, nothing is regenerated"


async def test_force_regenerates_what_already_exists(settings, written) -> None:
    course, lid, content = written
    counting = CountingEcho(settings)
    once = await enrich_lesson(counting, course=course, lesson_id=lid, content=content)
    before = counting.calls

    await enrich_lesson(counting, course=course, lesson_id=lid, content=once, force=True)

    assert counting.calls == before + len(LENSES) + 1


async def test_a_rerun_fills_only_the_lens_that_failed_before(settings, written) -> None:
    course, lid, content = written
    partial = await enrich_lesson(
        CountingEcho(settings, fail_lenses=["rigor"]), course=course, lesson_id=lid, content=content
    )
    assert "rigor" not in partial.lenses and partial.faq

    healthy = CountingEcho(settings)
    full = await enrich_lesson(healthy, course=course, lesson_id=lid, content=partial)

    assert set(full.lenses) == set(LENSES)
    assert healthy.calls == 1, "only the missing lens is written"
    assert full.faq == partial.faq


async def test_a_failed_faq_does_not_discard_good_lenses(settings, written) -> None:
    class NoFaq(EchoProvider):
        def _reply(self, messages: Sequence[Message]) -> str:
            if "[[SS:FAQ]]" in messages[-1].content:
                return "[]"
            return super()._reply(messages)

    course, lid, content = written
    enriched = await enrich_lesson(NoFaq(settings), course=course, lesson_id=lid, content=content)

    assert set(enriched.lenses) == set(LENSES)
    assert enriched.faq == []


async def test_flags_select_the_passes(provider, written) -> None:
    course, lid, content = written
    only_faq = await enrich_lesson(
        provider, course=course, lesson_id=lid, content=content, lenses=False
    )
    assert only_faq.faq and only_faq.lenses == {}


def test_provenance_only_claims_passes_complete_on_every_lesson() -> None:
    full = LessonContent(lenses={k: "x" for k in LENSES}, faq=[FaqItem(q="q", a="a")])
    no_faq = LessonContent(lenses={k: "x" for k in LENSES})

    assert enriched_passes([full, full]) == ["lenses", "faq"]
    assert enriched_passes([full, no_faq]) == ["lenses"]
    assert enriched_passes([]) == []


# --- API ------------------------------------------------------------------


def _course_with_one_lesson(client, syllabus: str) -> tuple[str, str]:
    course = client.post("/api/v1/courses", json={"syllabus": syllabus}).json()
    cid, lid = course["id"], course["modules"][0]["lessons"][0]["id"]
    assert client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate").status_code == 201
    return cid, lid


def test_enrich_endpoint_stores_lenses_and_faq(client, syllabus: str) -> None:
    cid, lid = _course_with_one_lesson(client, syllabus)
    before = client.get(f"/api/v1/courses/{cid}/lessons/{lid}").json()

    res = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/enrich")
    assert res.status_code == 200, res.text

    stored = client.get(f"/api/v1/courses/{cid}/lessons/{lid}").json()
    assert set(stored["lenses"]) == set(LENSES)
    assert stored["faq"] and stored["faq"][0]["q"]
    assert stored["generatedAt"] == before["generatedAt"]


def test_enrich_endpoint_honours_the_pass_flags(client, syllabus: str) -> None:
    cid, lid = _course_with_one_lesson(client, syllabus)
    body = client.post(f"/api/v1/courses/{cid}/lessons/{lid}/enrich?lenses=false").json()
    assert body["lenses"] == {} and body["faq"]


def test_enriching_an_unwritten_lesson_is_404(client, syllabus: str) -> None:
    course = client.post("/api/v1/courses", json={"syllabus": syllabus}).json()
    lid = course["modules"][0]["lessons"][0]["id"]
    assert client.post(f"/api/v1/courses/{course['id']}/lessons/{lid}/enrich").status_code == 404


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_bulk_enrich_streams_progress_then_done(client, syllabus: str) -> None:
    cid, lid = _course_with_one_lesson(client, syllabus)
    total = sum(len(m["lessons"]) for m in client.get(f"/api/v1/courses/{cid}").json()["modules"])

    events = _events(client.post(f"/api/v1/courses/{cid}/enrich").text)

    names = [name for name, _ in events]
    assert names[-1] == "done" and set(names[:-1]) == {"progress"}
    outcomes = {
        p["lessonId"]: p["stage"] for n, p in events if n == "progress" and p["stage"] != "started"
    }
    assert outcomes[lid] == "enriched"
    assert list(outcomes.values()).count("unwritten") == total - 1
    progress = [p for n, p in events if n == "progress"]
    assert all({"lessonId", "stage", "done", "total"} <= set(p) for p in progress)
    assert events[-1][1]["outcomes"] == {"enriched": 1, "unwritten": total - 1}

    stored = client.get(f"/api/v1/courses/{cid}/lessons/{lid}").json()
    assert set(stored["lenses"]) == set(LENSES)


def test_bulk_enrich_with_no_author_model_is_503_before_streaming(
    tmp_path: Path, syllabus: str
) -> None:
    from fastapi.testclient import TestClient

    from syllabus_studio.app import create_app

    s = Settings(
        _env_file=None,
        llm_provider="echo",
        author_provider="none",
        reader_provider="",
        db_path=tmp_path / "t.db",
        seed_demo_course=True,
    )
    with TestClient(create_app(s)) as client:
        cid = client.get("/api/v1/courses").json()[0]["id"]
        res = client.post(f"/api/v1/courses/{cid}/enrich")
    assert res.status_code == 503
    assert res.json()["code"] == "not_configured"


# --- CLI ------------------------------------------------------------------


def test_publish_writes_enriches_and_stamps_provenance(
    monkeypatch, settings: Settings, syllabus: str, tmp_path: Path
) -> None:
    from syllabus_studio import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    source = tmp_path / "syllabus.txt"
    source.write_text(syllabus, encoding="utf-8")
    assert cli.main(["build", str(source), "--name", "Applied ML"]) == 0

    async def only_course_id() -> str:
        from syllabus_studio.storage import get_store

        store = get_store(settings)
        await store.startup()
        try:
            return (await store.list_courses())[0].id
        finally:
            await store.shutdown()

    cid = asyncio.run(only_course_id())
    out = tmp_path / "applied-ml.course.json"

    assert cli.main(["publish", cid, "-o", str(out), "--reviewed-by", "Ada"]) == 0

    from syllabus_studio.storage import CourseBundle

    bundle = CourseBundle.read(out)
    lesson_ids = [ls.id for _, _, ls in bundle.course.all_lessons()]
    assert sorted(bundle.lessons) == sorted(lesson_ids), "publish writes every missing lesson"
    assert all(set(c.lenses) == set(LENSES) and c.faq for c in bundle.lessons.values())
    p = bundle.provenance
    assert p.enriched == ["lenses", "faq"]
    assert p.author_provider == "echo" and p.generated_at > 0
    assert p.human_reviewed is True and p.reviewer == "Ada"
    assert bundle.course.progress == {}, "bundles carry lessons, never progress"


def test_publish_without_a_reviewer_says_unreviewed(
    monkeypatch, settings: Settings, syllabus: str, tmp_path: Path
) -> None:
    from syllabus_studio import cli
    from syllabus_studio.storage import CourseBundle

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    source = tmp_path / "s.txt"
    source.write_text(syllabus, encoding="utf-8")
    cli.main(["build", str(source)])
    cid = cli.asyncio.run(cli._with_store(lambda _s, st: st.list_courses()))[0].id
    out = tmp_path / "b.json"

    assert cli.main(["publish", cid, "-o", str(out)]) == 0
    assert CourseBundle.read(out).provenance.human_reviewed is False
