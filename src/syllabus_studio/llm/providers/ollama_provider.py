"""Ollama provider — local models, and Ollama's hosted models, over one API.

Ollama speaks the same HTTP protocol whether the model runs on your machine or
in Ollama's cloud, so this one class covers both. What changes is `SS_OLLAMA_HOST`:

* ``http://localhost:11434`` (default) — the local daemon. It serves models you
  have pulled, and, once you have run ``ollama signin``, also proxies Ollama's
  cloud models (their names end in ``-cloud``). Nothing else to configure.
* ``https://ollama.com`` — talk to the hosted endpoint directly, no daemon
  required. Set ``OLLAMA_API_KEY`` as well.

Only ``httpx`` is needed, which the project already depends on — there is no
``ollama`` SDK in the dependency list and this deliberately does not add one.

Endpoints used (both stable parts of Ollama's API):
    POST /api/chat   {model, messages, stream, format?, options, keep_alive}
    GET  /api/tags   -> {"models": [{"name": "qwen2.5:14b", ...}, ...]}
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from syllabus_studio.config import Settings

from ..base import BaseProvider, LLMError, Message, ModelTier
from ..registry import register

PROBE_TIMEOUT_S = 2.5


def _to_api(messages: Sequence[Message]) -> list[dict[str, str]]:
    """Collapse consecutive same-role turns — some local models handle it badly."""
    out: list[dict[str, str]] = []
    for m in messages:
        if out and out[-1]["role"] == m.role:
            out[-1]["content"] += "\n\n" + m.content
        else:
            out.append({"role": m.role, "content": m.content})
    if not out or out[0]["role"] != "user":
        raise LLMError("invalid_request", "The conversation must start with a user turn.")
    return out


@register("ollama")
class OllamaProvider(BaseProvider):
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.host = settings.ollama_host.rstrip("/")
        self._client = client
        self._owns_client = client is None

    # -- plumbing ----------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.settings.ollama_api_key:
                headers["Authorization"] = f"Bearer {self.settings.ollama_api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.host,
                timeout=httpx.Timeout(self.settings.request_timeout_s, connect=10.0),
                headers=headers,
            )
        return self._client

    def _is_hosted(self) -> bool:
        return not self.host.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]"))

    def _body(
        self,
        messages: Sequence[Message],
        tier: ModelTier,
        *,
        stream: bool,
        max_tokens: int | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.settings.model_for_tier(tier, "ollama"),
            "messages": _to_api(messages),
            "stream": stream,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {"num_predict": max_tokens or self.settings.max_output_tokens},
        }
        if json_mode:
            # Ollama constrains decoding to valid JSON. Small local models need
            # this — without it they narrate around the object and the parse fails.
            body["format"] = "json"
        return body

    def _translate(self, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.ConnectError):
            if self._is_hosted():
                return LLMError("upstream_error", f"Could not reach {self.host}.")
            return LLMError(
                "not_configured",
                f"No Ollama daemon at {self.host}. Start it with `ollama serve`, "
                "or set SS_LLM_PROVIDER=echo to work offline.",
            )
        if isinstance(exc, httpx.ReadTimeout):
            return LLMError(
                "upstream_error",
                "Ollama timed out. A large model on a cold start can exceed the timeout — "
                "raise SS_REQUEST_TIMEOUT_S, or pick a smaller model for this tier.",
            )
        if isinstance(exc, httpx.HTTPError):
            return LLMError("upstream_error", f"Ollama request failed: {exc}")
        return LLMError("upstream_error", str(exc))

    def _translate_status(self, status: int, text: str, model: str) -> LLMError:
        detail = text.strip()[:300]
        try:
            detail = json.loads(text).get("error", detail)
        except Exception:  # noqa: BLE001
            pass

        if status == 404 or "not found" in detail.lower():
            return LLMError(
                "not_configured",
                f"Ollama has no model named {model!r}. Pull it with `ollama pull {model}`, "
                "or point SS_OLLAMA_MODEL_DEFAULT at one from `ollama list`.",
            )
        if status in (401, 403):
            return LLMError(
                "not_configured",
                "Ollama rejected the credentials. Set OLLAMA_API_KEY for a hosted endpoint, "
                "or run `ollama signin` to use cloud models through the local daemon.",
            )
        if status == 429:
            return LLMError("rate_limited", "Ollama is rate limiting. Wait a moment and retry.")
        if status == 413:
            return LLMError("prompt_too_large", "The prompt exceeded the model's context window.")
        return LLMError("upstream_error", f"Ollama returned {status}: {detail}")

    # -- the contract ------------------------------------------------------

    async def _chat(
        self,
        messages: Sequence[Message],
        tier: ModelTier,
        *,
        max_tokens: int | None,
        json_mode: bool,
    ) -> str:
        body = self._body(messages, tier, stream=False, max_tokens=max_tokens, json_mode=json_mode)
        try:
            resp = await self.client.post("/api/chat", json=body)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

        if resp.status_code >= 400:
            raise self._translate_status(resp.status_code, resp.text, body["model"])

        payload = resp.json()
        text = (payload.get("message") or {}).get("content", "")
        if not text.strip():
            raise LLMError("empty_completion", "Ollama returned no text.")
        return text

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> str:
        return await self._chat(messages, tier, max_tokens=max_tokens, json_mode=False)

    async def complete_json(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> Any:
        """Same as the base, but asks Ollama to constrain decoding to JSON first."""
        from ..base import extract_json

        text = await self._chat(messages, tier, max_tokens=max_tokens, json_mode=True)
        return extract_json(text)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tier: ModelTier = "default",
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        body = self._body(messages, tier, stream=True, max_tokens=max_tokens, json_mode=False)
        try:
            async with self.client.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise self._translate_status(resp.status_code, resp.text, body["model"])

                # Ollama streams newline-delimited JSON, one object per chunk.
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise LLMError("upstream_error", str(chunk["error"]))
                    piece = (chunk.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    # -- health ------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": True,  # probe() has the real answer
            "host": self.host,
            "mode": "hosted" if self._is_hosted() else "local",
            "models": self.settings.models_for("ollama"),
        }

    async def probe(self) -> dict[str, Any]:
        """Ask the daemon what it has. Never raises — /health must always answer."""
        wanted = self.settings.models_for("ollama")
        try:
            resp = await self.client.get("/api/tags", timeout=PROBE_TIMEOUT_S)
            resp.raise_for_status()
            installed = [m.get("name", "") for m in (resp.json().get("models") or [])]
        except Exception as exc:  # noqa: BLE001
            hint = (
                f"Could not reach {self.host}."
                if self._is_hosted()
                else f"No Ollama daemon at {self.host} — start it with `ollama serve`."
            )
            return {"available": False, "reachable": False, "detail": f"{hint} ({type(exc).__name__})"}

        # `ollama list` shows "name:tag"; a bare name means the :latest tag.
        def present(model: str) -> bool:
            return model in installed or f"{model}:latest" in installed

        missing = sorted({m for m in wanted.values() if not present(m)})
        return {
            "available": not missing,
            "reachable": True,
            "installedModels": sorted(installed),
            "missingModels": missing,
            "detail": (
                f"Not pulled: {', '.join(missing)} — run `ollama pull {missing[0]}`"
                if missing
                else ""
            ),
        }

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
