from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from syllabus_studio import __version__
from syllabus_studio.core.tutor import available_lenses
from syllabus_studio.llm import BaseProvider

from ..deps import AuthorProviderDep, ReaderProviderDep, StoreDep
from ..schemas import Capabilities, HealthResponse

router = APIRouter(tags=["meta"])


async def _status(provider: BaseProvider) -> dict[str, Any]:
    """``describe()`` is static and free; ``probe()`` is the live check a provider
    may implement (is the daemon up, is the model pulled) and its answer wins.
    A provider that cannot fail this way returns nothing and the merge is a no-op.
    """
    info = dict(provider.describe())
    try:
        info.update(await provider.probe())
    except Exception as exc:  # noqa: BLE001  (health must always answer)
        info["available"] = False
        info["detail"] = f"Provider probe failed: {exc}"
    return info


@router.get("/health", response_model=HealthResponse)
async def health(
    author: AuthorProviderDep, reader: ReaderProviderDep, store: StoreDep
) -> HealthResponse:
    """What is actually wired up right now, per role."""

    # Same provider for both roles (the common, single-knob case): probe once.
    # Both share one Settings, so the answer can't differ.
    if author.name == reader.name:
        author_info = await _status(author)
        reader_info = dict(author_info)
    else:
        author_info, reader_info = await asyncio.gather(_status(author), _status(reader))

    capabilities = Capabilities(
        author_courses=bool(author_info.get("available")),
        live_tutor=bool(reader_info.get("available")),
        live_lenses=bool(reader_info.get("available")),
    )
    return HealthResponse(
        version=__version__,
        llm={"author": author_info, "reader": reader_info},
        storage=store.describe(),
        lenses=available_lenses(),
        capabilities=capabilities,
    )
