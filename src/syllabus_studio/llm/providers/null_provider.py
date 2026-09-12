"""The ``none`` provider: deliberately no model.

Exists so "no reader model" is an ordinary provider that reports itself
unavailable, rather than a ``None`` every call site has to branch on.
Capability detection then reads ``describe()`` the same way for every role.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, NoReturn

from syllabus_studio.config import Settings

from ..base import BaseProvider, LLMError, Message, ModelTier
from ..registry import register


def _refuse() -> NoReturn:
    raise LLMError(
        "not_configured",
        "No model is configured for this. Set SS_LLM_PROVIDER (or the per-role "
        "SS_AUTHOR_PROVIDER / SS_READER_PROVIDER) to a real provider.",
    )


@register("none")
class NullProvider(BaseProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> str:
        _refuse()

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        _refuse()
        yield ""  # unreachable; makes this an async generator like the others

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "available": False}
