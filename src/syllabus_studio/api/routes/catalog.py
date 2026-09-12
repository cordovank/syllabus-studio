from __future__ import annotations

from fastapi import APIRouter, status

from syllabus_studio.core.models import Course, LessonProgress
from syllabus_studio.storage import CatalogError, fetch_bundle, load_catalog
from syllabus_studio.storage.catalog import Catalog

from ..deps import SettingsDep, StoreDep

router = APIRouter(tags=["catalog"])


@router.get("/catalog", response_model=Catalog)
async def get_catalog(settings: SettingsDep) -> Catalog:
    return await load_catalog(settings.catalog_url)


@router.post("/catalog/{entry_id}/install", response_model=Course, status_code=status.HTTP_201_CREATED)
async def install_entry(entry_id: str, settings: SettingsDep, store: StoreDep) -> Course:
    catalog = await load_catalog(settings.catalog_url)
    entry = catalog.find(entry_id)
    if entry is None:
        raise CatalogError(f"No catalog entry {entry_id!r}.")

    bundle = await fetch_bundle(entry, catalog_url=settings.catalog_url)
    course = bundle.materialise(origin=f"catalog:{entry_id}")
    saved = await store.save_course(course)

    for lesson_id, content in bundle.lessons.items():
        if saved.find(lesson_id) is None:
            continue
        await store.save_lesson(saved.id, lesson_id, content)
        saved.progress.setdefault(lesson_id, LessonProgress()).built = True

    return await store.save_course(saved)
