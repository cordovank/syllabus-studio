"""Authoring passes: work done once, on the author's model, and shipped in the bundle.

Nothing here runs at read time. A lens is a pure function of (lesson, lens id)
and likely questions are predictable, so both are precomputed on a strong model
and stored in ``LessonContent`` for readers who have a weak model or none.

Cost, for a contributor about to run this: 4 short lens generations and 1 FAQ
per lesson — about 60 calls for a 12-lesson course, on top of the 13 that built
it. Minutes and cents on a hosted model.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Collection, Iterable
from typing import Any

from syllabus_studio.llm import BaseProvider, LLMError, Message

from .models import Course, FaqItem, Lesson, LessonContent
from .prompts import LENSES, faq_prompt, lens_prompt

log = logging.getLogger(__name__)

LENS_CONCURRENCY = 2
PASSES = ("lenses", "faq")


class AuthoringError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _lesson(course: Course, lesson_id: str) -> Lesson:
    found = course.find(lesson_id)
    if found is None:
        raise AuthoringError("not_found", f"No lesson {lesson_id!r} in this course.")
    return found[2]


def _as_authoring_error(exc: Exception) -> AuthoringError:
    if isinstance(exc, AuthoringError):
        return exc
    if isinstance(exc, LLMError):
        return AuthoringError(exc.code, exc.message)
    return AuthoringError("upstream_error", str(exc))


# --------------------------------------------------------------------------
# lenses
# --------------------------------------------------------------------------


async def precompute_lenses(
    provider: BaseProvider,
    *,
    course: Course,
    lesson_id: str,
    content: LessonContent,
    only: Collection[str] | None = None,
) -> dict[str, str]:
    """Write every lens (or just ``only``) for one lesson.

    The prompt is ``lens_prompt`` verbatim, so a stored lens is the same kind of
    text the live path would stream; if the two ever diverged, readers with and
    without a model would get different courses.

    A lens that fails is logged and left out; the rest are kept. Only when every
    attempted lens fails does this raise — one timeout must not cost all four.
    """
    lesson = _lesson(course, lesson_id)
    wanted = [lens for lens in LENSES if only is None or lens in only]
    if not wanted:
        return {}

    gate = asyncio.Semaphore(LENS_CONCURRENCY)

    async def one(lens: str) -> str:
        prompt = lens_prompt(course=course, lesson=lesson, content=content, lens=lens)
        async with gate:
            text = await provider.complete([Message.user(prompt)], tier="default")
        if not text.strip():
            raise LLMError("empty_completion", f"The model returned nothing for lens {lens!r}.")
        return text.strip()

    results = await asyncio.gather(*(one(lens) for lens in wanted), return_exceptions=True)

    out: dict[str, str] = {}
    errors: list[Exception] = []
    for lens, result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result  # cancellation and friends are not a lens failure
            log.warning("Lens %r failed for %s/%s: %s", lens, course.id, lesson_id, result)
            errors.append(result)
        else:
            out[lens] = result

    if not out and errors:
        raise _as_authoring_error(errors[0])
    return out


# --------------------------------------------------------------------------
# faq
# --------------------------------------------------------------------------


def _faq_items(payload: Any, count: int) -> list[FaqItem]:
    """Accept a bare array, or an object wrapping one.

    The wrapper case is not hypothetical: Ollama's ``format: "json"`` pushes
    small models towards a top-level object even when asked for an array.
    """
    rows: Any = payload
    if isinstance(payload, dict):
        rows = next((v for v in payload.values() if isinstance(v, list)), [])
    if not isinstance(rows, list):
        return []

    items: list[FaqItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        q = str(row.get("q") or row.get("question") or "").strip()
        a = str(row.get("a") or row.get("answer") or "").strip()
        if q and a:
            items.append(FaqItem(q=q, a=a))
    return items[:count]


async def generate_faq(
    provider: BaseProvider,
    *,
    course: Course,
    lesson_id: str,
    content: LessonContent,
    count: int = 10,
) -> list[FaqItem]:
    lesson = _lesson(course, lesson_id)
    prompt = faq_prompt(course=course, lesson=lesson, content=content, count=count)
    try:
        payload = await provider.complete_json([Message.user(prompt)], tier="default")
    except LLMError as exc:
        raise _as_authoring_error(exc) from exc

    items = _faq_items(payload, count)
    if not items:
        raise AuthoringError("invalid_json", "The model returned no usable questions and answers.")
    return items


# --------------------------------------------------------------------------
# a whole lesson
# --------------------------------------------------------------------------


def missing_enrichment(
    content: LessonContent, *, lenses: bool = True, faq: bool = True
) -> list[str]:
    """Which requested passes this lesson still lacks. Empty means complete."""
    missing = []
    if lenses and any(lens not in content.lenses for lens in LENSES):
        missing.append("lenses")
    if faq and not content.faq:
        missing.append("faq")
    return missing


async def enrich_lesson(
    provider: BaseProvider,
    *,
    course: Course,
    lesson_id: str,
    content: LessonContent,
    lenses: bool = True,
    faq: bool = True,
    force: bool = False,
) -> LessonContent:
    """Return a copy of ``content`` with lenses and/or FAQ filled in.

    Never touches ``sections``, ``quiz``, ``practice`` or ``generated_at``:
    enrichment decorates a lesson, it does not rewrite it, so progress stays valid.

    Without ``force`` only what is missing is written — the same rule as lessons,
    where ``force`` is the only thing that spends tokens twice. That is also what
    makes re-running ``publish`` after a partial failure cheap.

    A pass that fails leaves its field as it was and is logged. This raises only
    when every pass it attempted failed, so a bad FAQ never discards good lenses.
    """
    _lesson(course, lesson_id)
    lens_ids = [lens for lens in LENSES if force or lens not in content.lenses] if lenses else []
    want_faq = faq and (force or not content.faq)

    new_lenses: dict[str, str] = {}
    new_faq: list[FaqItem] | None = None
    errors: list[AuthoringError] = []

    if lens_ids:
        try:
            new_lenses = await precompute_lenses(
                provider, course=course, lesson_id=lesson_id, content=content, only=lens_ids
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(_as_authoring_error(exc))

    if want_faq:
        try:
            new_faq = await generate_faq(
                provider, course=course, lesson_id=lesson_id, content=content
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("FAQ failed for %s/%s: %s", course.id, lesson_id, exc)
            errors.append(_as_authoring_error(exc))

    attempted = bool(lens_ids) + bool(want_faq)
    if attempted and len(errors) == attempted:
        raise errors[0]

    # Merge rather than replace, so a lens that failed this time keeps the text
    # it had from an earlier run.
    return content.model_copy(
        deep=True,
        update={
            "lenses": {**content.lenses, **new_lenses},
            "faq": new_faq if new_faq is not None else list(content.faq),
        },
    )


# --------------------------------------------------------------------------
# a whole course
# --------------------------------------------------------------------------


async def enrich_course(
    provider: BaseProvider,
    store: Any,
    *,
    course: Course,
    lenses: bool = True,
    faq: bool = True,
    force: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Enrich every written lesson, in outline order, saving each as it lands.

    Yields one ``started`` and one outcome event per lesson. Outcomes:
    ``enriched`` (nothing missing), ``partial`` (``missing`` says what),
    ``failed`` (``code``/``message``), ``unwritten`` (nothing to enrich yet).
    One lesson failing never stops the rest — this runs for minutes.

    ``store`` is a ``CourseStore``; typed loosely to keep core free of storage imports.
    """
    ordered = [ls.id for _, _, ls in course.all_lessons()]
    total = len(ordered)

    for done, lesson_id in enumerate(ordered, start=1):
        base = {"lessonId": lesson_id, "done": done, "total": total}
        content = await store.get_lesson(course.id, lesson_id)
        if content is None:
            yield {**base, "stage": "unwritten"}
            continue

        yield {**base, "done": done - 1, "stage": "started"}
        try:
            enriched = await enrich_lesson(
                provider,
                course=course,
                lesson_id=lesson_id,
                content=content,
                lenses=lenses,
                faq=faq,
                force=force,
            )
        except AuthoringError as exc:
            yield {**base, "stage": "failed", "code": exc.code, "message": exc.message}
            continue

        if enriched != content:
            await store.save_lesson(course.id, lesson_id, enriched)
        missing = missing_enrichment(enriched, lenses=lenses, faq=faq)
        if missing:
            yield {**base, "stage": "partial", "missing": missing}
        else:
            yield {**base, "stage": "enriched"}


def enriched_passes(lessons: Iterable[LessonContent]) -> list[str]:
    """The passes that are complete on *every* given lesson — what provenance may claim.

    Derived from the content rather than from which flags were passed, so a
    bundle never says "faq" when one lesson's FAQ quietly failed.
    """
    rows = list(lessons)
    if not rows:
        return []
    return [
        name
        for name in PASSES
        if all(
            not missing_enrichment(c, lenses=name == "lenses", faq=name == "faq") for c in rows
        )
    ]
