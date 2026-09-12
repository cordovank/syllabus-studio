"""The provider contract.

Everything above this layer (``core/``, ``api/``) talks only to
:class:`LLMProvider`.  Adding Vertex, OpenAI or another local model means
writing one subclass of :class:`BaseProvider` and registering it — no changes
to course logic.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

ModelTier = Literal["quick", "default", "complex"]

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str

    @staticmethod
    def user(content: str) -> Message:
        return Message("user", content)

    @staticmethod
    def assistant(content: str) -> Message:
        return Message("assistant", content)


class LLMError(Exception):
    """One failure shape, mirroring the codes the browser runtime used.

    Codes: ``not_configured``, ``rate_limited``, ``invalid_json``, ``refused``,
    ``empty_completion``, ``prompt_too_large``, ``upstream_error``, ``cancelled``.
    """

    def __init__(self, code: str, message: str, *, partial: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.partial = partial

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.partial:
            out["partial"] = self.partial
        return out


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Read a JSON value out of a model reply, tolerantly.

    Tries, in order: the whole reply; the body of the first fenced block; the
    span from the first ``{``/``[`` to the last ``}``/``]``.  Raises
    :class:`LLMError` with code ``invalid_json`` when nothing parses.
    """
    candidates: list[str] = [text.strip()]

    fence = _FENCE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i != -1]
    if starts and ends:
        span = text[min(starts) : max(ends) + 1].strip()
        candidates.append(span)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise LLMError("invalid_json", "The model reply held no parseable JSON value.", partial=text)


class BaseProvider(ABC):
    """Implement :meth:`complete` and :meth:`stream`; the rest comes free."""

    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> str:
        """Return the whole reply."""

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield reply text deltas as they are written."""

    async def complete_json(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> Any:
        text = await self.complete(messages, tier=tier, max_tokens=max_tokens)
        if not text.strip():
            raise LLMError("empty_completion", "The model returned nothing.")
        return extract_json(text)

    def describe(self) -> dict[str, Any]:
        """Static facts about this provider: what it is, what it would call.

        Cheap and synchronous — never touches the network.
        """
        return {"provider": self.name, "available": True}

    async def probe(self) -> dict[str, Any]:
        """Optional live check, merged over :meth:`describe` by ``/health``.

        A provider that can actually be unreachable (a local daemon that is not
        running, a model that has not been pulled) overrides this and returns
        keys such as ``available``, ``reachable`` and ``detail``. Must never
        raise, and must be fast — the UI waits on it.
        """
        return {}
