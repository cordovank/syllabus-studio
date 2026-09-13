"""Publish a course into the site: the one implementation the CLI and the API share.

``core.publishing`` does the model work (write what's missing, enrich); this module
runs it, then hands the result to ``storage.site`` to stamp and write. It sits above
both on purpose — ``core`` must not learn about files, and ``storage`` should not
run models.

Events, in order, as ``(event, payload)`` pairs ready for SSE or printing:

    ("progress", {"phase": "write" | "enrich", ...})   see core.publishing
    ("published", report)                               see storage.site.publish_report

A lesson that fails to write raises ``PublishError`` before anything touches the site.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from syllabus_studio.core.models import Course
from syllabus_studio.core.publishing import PublishError, prepare_course
from syllabus_studio.llm import BaseProvider
from syllabus_studio.storage import CourseStore
from syllabus_studio.storage.site import (
    publish_report,
    publish_to_site,
    site_bundle,
    stamp_provenance,
)

__all__ = ["PublishError", "author_model_name", "publish_course"]


def author_model_name(provider: BaseProvider) -> str:
    """The model to name in provenance: what the provider says it calls for the
    default tier, else the provider itself.
    """
    models = provider.describe().get("models") or {}
    if models.get("default"):
        return str(models["default"])
    return "" if provider.name == "none" else provider.name


async def publish_course(
    provider: BaseProvider,
    store: CourseStore,
    *,
    course: Course,
    site_dir: Path,
    model_name: str,
    reviewer: str = "",
    force: bool = False,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    async for event in prepare_course(
        provider, store, course=course, model_name=model_name, force=force
    ):
        if event["phase"] != "ready":
            yield "progress", event

    bundle = await site_bundle(store, course.id)
    if bundle is None:  # deleted while it was being prepared
        raise PublishError("not_found", f"No course {course.id!r}.")

    stamped = stamp_provenance(
        bundle,
        author_provider=provider.name,
        author_model=model_name,
        reviewer=reviewer.strip(),
        published=True,
    )
    entry = publish_to_site(site_dir, stamped)

    # The course keeps what it was published with, so a later export says so too.
    saved = await store.get_course(course.id)
    if saved is not None:
        saved.provenance = stamped.provenance
        await store.save_course(saved)

    yield "published", publish_report(stamped, entry, site_dir)
