"""Community catalog — an open index of shareable course bundles.

The catalog is deliberately dumb: a single JSON document listing entries, each
pointing at a bundle URL.  Anyone can host one (a GitHub raw URL is enough),
and ``SS_CATALOG_URL`` decides which one this install reads.  A local
``catalog.json`` ships as an example and as the offline fallback.

    {
      "name": "Community courses",
      "entries": [
        {"id": "applied-ml", "title": "Applied Machine Learning",
         "description": "...", "author": "...", "tags": ["ml"],
         "url": "https://.../applied-ml.course.json"}
      ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .bundle import CourseBundle

LOCAL_CATALOG = Path(__file__).resolve().parents[1] / "data" / "catalog.json"


class CatalogEntry(BaseModel):
    id: str
    title: str
    description: str = ""
    author: str = ""
    license: str = ""
    tags: list[str] = Field(default_factory=list)
    url: str = ""
    """Absolute URL of the bundle, or a path relative to the catalog document."""


class Catalog(BaseModel):
    name: str = "Community courses"
    source: str = ""
    entries: list[CatalogEntry] = Field(default_factory=list)

    def find(self, entry_id: str) -> CatalogEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)


class CatalogError(Exception):
    pass


def _resolve(base: str, url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if not base:
        return url
    return base.rsplit("/", 1)[0] + "/" + url.lstrip("./")


async def load_catalog(url: str = "", *, timeout: float = 15.0) -> Catalog:
    """Fetch the configured catalog, falling back to the bundled example."""
    if not url:
        if LOCAL_CATALOG.exists():
            data: dict[str, Any] = json.loads(LOCAL_CATALOG.read_text(encoding="utf-8"))
            return Catalog(**data, source="local")
        return Catalog(entries=[], source="none")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise CatalogError(f"Could not read the catalog at {url}: {exc}") from exc

    catalog = Catalog(**payload)
    catalog.source = url
    return catalog


async def fetch_bundle(entry: CatalogEntry, *, catalog_url: str = "", timeout: float = 30.0) -> CourseBundle:
    target = _resolve(catalog_url, entry.url)
    if not target:
        raise CatalogError(f"Catalog entry {entry.id!r} has no bundle URL.")

    if not target.startswith(("http://", "https://")):
        path = Path(target)
        if not path.is_absolute():
            path = LOCAL_CATALOG.parent / path
        if not path.exists():
            raise CatalogError(f"Bundle file not found: {path}")
        return CourseBundle.read(path)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(target)
            resp.raise_for_status()
            return CourseBundle.parse(resp.text)
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CatalogError(f"Could not download the bundle at {target}: {exc}") from exc
