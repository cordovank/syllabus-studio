"""Anthropic API provider."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from syllabus_studio.config import Settings

from ..base import BaseProvider, LLMError, Message, ModelTier
from ..registry import register


def _to_api(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Collapse consecutive same-role turns; the API wants them alternating."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content})
    if not out or out[0]["role"] != "user":
        raise LLMError("invalid_request", "The conversation must start with a user turn.")
    return out


def _translate(exc: Exception) -> LLMError:
    import anthropic

    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limited", "Rate limit or usage cap reached. Try again shortly.")
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("not_configured", "The API key was rejected. Check ANTHROPIC_API_KEY.")
    if isinstance(exc, anthropic.BadRequestError):
        text = str(exc)
        if "too long" in text or "max_tokens" in text:
            return LLMError("prompt_too_large", "The prompt exceeded the model's context window.")
        return LLMError("invalid_request", text)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("upstream_error", "Could not reach the Anthropic API.")
    return LLMError("upstream_error", str(exc))


@register("anthropic")
class AnthropicProvider(BaseProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            key = self.settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise LLMError(
                    "not_configured",
                    "ANTHROPIC_API_KEY is not set. Add it to .env, or set SS_LLM_PROVIDER=echo "
                    "to run against the offline stub.",
                )
            self._client = anthropic.AsyncAnthropic(
                api_key=key, timeout=self.settings.request_timeout_s
            )
        return self._client

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> str:
        try:
            resp = await self.client.messages.create(
                model=self.settings.model_for_tier(tier),
                max_tokens=max_tokens or self.settings.max_output_tokens,
                messages=_to_api(messages),
            )
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMError("refused", "The model declined this input.")
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        text = "".join(parts)
        if not text.strip():
            raise LLMError("empty_completion", "The model returned no text.")
        return text

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        try:
            async with self.client.messages.stream(
                model=self.settings.model_for_tier(tier),
                max_tokens=max_tokens or self.settings.max_output_tokens,
                messages=_to_api(messages),
            ) as stream:
                async for delta in stream.text_stream:
                    if delta:
                        yield delta
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": bool(self.settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")),
            "models": self.settings.models_for("anthropic"),
        }
