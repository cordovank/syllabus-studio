"""Storage backends and the portable bundle format."""

from __future__ import annotations

from pathlib import Path

from syllabus_studio.config import Settings, get_settings

from .base import CourseStore, StorageError
from .bundle import CourseBundle, Provenance, suggested_filename
from .catalog import Catalog, CatalogEntry, CatalogError, fetch_bundle, load_catalog
from .remote_store import RemoteCourseStore
from .sqlite_store import SQLiteCourseStore

__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "CourseBundle",
    "CourseStore",
    "Provenance",
    "RemoteCourseStore",
    "SQLiteCourseStore",
    "StorageError",
    "fetch_bundle",
    "get_store",
    "load_catalog",
    "suggested_filename",
]


def get_store(settings: Settings | None = None) -> CourseStore:
    """Build the store named by ``SS_STORAGE_BACKEND``."""
    settings = settings or get_settings()
    backend = settings.storage_backend

    if backend == "sqlite":
        return SQLiteCourseStore(Path(settings.db_path))
    if backend == "remote":
        return RemoteCourseStore(settings.remote_url, settings.remote_token)
    raise ValueError(f"Unknown storage backend {backend!r}. Use 'sqlite' or 'remote'.")
