from __future__ import annotations

from fastapi import APIRouter

from syllabus_studio import __version__
from syllabus_studio.core.tutor import available_lenses

from ..deps import ProviderDep, StoreDep
from ..schemas import HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
async def health(provider: ProviderDep, store: StoreDep) -> HealthResponse:
    """What is actually wired up right now.

    ``describe()`` is static and free; ``probe()`` is the live check a provider
    may implement (is the daemon up, is the model pulled) and its answer wins.
    A provider that cannot fail this way returns nothing and the merge is a
    no-op.
    """
    llm = dict(provider.describe())
    try:
        llm.update(await provider.probe())
    except Exception as exc:  # noqa: BLE001  (health must always answer)
        llm["available"] = False
        llm["detail"] = f"Provider probe failed: {exc}"

    return HealthResponse(
        version=__version__,
        llm=llm,
        storage=store.describe(),
        lenses=available_lenses(),
        can_generate=bool(llm.get("available")),
    )
