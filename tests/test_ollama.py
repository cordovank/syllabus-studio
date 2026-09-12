"""Ollama provider tests, against a mocked daemon.

No Ollama needed: an ``httpx.MockTransport`` stands in for the HTTP API, so
these run in CI and on a machine that has never installed it. Each test asserts
on the request the provider actually sent, which is the part that breaks when
someone edits the body builder.
"""

from __future__ import annotations

import json

import httpx
import pytest

from syllabus_studio.config import Settings
from syllabus_studio.llm import LLMError, Message
from syllabus_studio.llm.providers.ollama_provider import OllamaProvider

SENT: list[httpx.Request] = []


def make_provider(handler, **overrides) -> OllamaProvider:
    """A provider wired to a fake daemon. ``handler(request) -> httpx.Response``."""
    SENT.clear()

    def record(request: httpx.Request) -> httpx.Response:
        SENT.append(request)
        return handler(request)

    settings = Settings(_env_file=None, llm_provider="ollama", **overrides)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(record), base_url=settings.ollama_host.rstrip("/")
    )
    return OllamaProvider(settings, client=client)


def chat_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": text}, "done": True})


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


# --- the happy paths ------------------------------------------------------


async def test_complete_sends_a_well_formed_chat_request() -> None:
    p = make_provider(lambda r: chat_reply("hello from a local model"))

    out = await p.complete([Message.user("hi")], tier="default")

    assert out == "hello from a local model"
    assert str(SENT[0].url).endswith("/api/chat")
    body = body_of(SENT[0])
    assert body["model"] == "qwen2.5:14b"
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["options"]["num_predict"] > 0
    assert "format" not in body, "plain completions must not be constrained to JSON"


async def test_tier_selects_the_configured_model() -> None:
    p = make_provider(lambda r: chat_reply("x"), ollama_model_quick="tinyllama")
    await p.complete([Message.user("hi")], tier="quick")
    assert body_of(SENT[0])["model"] == "tinyllama"


async def test_json_mode_is_requested_for_structured_calls() -> None:
    p = make_provider(lambda r: chat_reply('{"title": "A course"}'))

    data = await p.complete_json([Message.user("give me json")])

    assert data == {"title": "A course"}
    assert body_of(SENT[0])["format"] == "json", (
        "small local models need constrained decoding or they narrate around the object"
    )


async def test_consecutive_same_role_turns_are_merged() -> None:
    p = make_provider(lambda r: chat_reply("ok"))
    await p.complete([Message.user("standing rules"), Message.user("the question")])

    messages = body_of(SENT[0])["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "standing rules\n\nthe question"


async def test_streaming_yields_each_chunk() -> None:
    lines = [
        json.dumps({"message": {"content": "Because "}, "done": False}),
        json.dumps({"message": {"content": "the label "}, "done": False}),
        json.dumps({"message": {"content": "moved."}, "done": True}),
    ]
    p = make_provider(lambda r: httpx.Response(200, text="\n".join(lines) + "\n"))

    chunks = [c async for c in p.stream([Message.user("why?")])]

    assert chunks == ["Because ", "the label ", "moved."]
    assert body_of(SENT[0])["stream"] is True


async def test_streaming_ignores_keepalive_blank_lines() -> None:
    payload = "\n\n" + json.dumps({"message": {"content": "ok"}, "done": True}) + "\n\n"
    p = make_provider(lambda r: httpx.Response(200, text=payload))
    assert [c async for c in p.stream([Message.user("hi")])] == ["ok"]


# --- the failure paths ----------------------------------------------------


async def test_a_missing_model_says_how_to_pull_it() -> None:
    p = make_provider(lambda r: httpx.Response(404, json={"error": 'model "qwen2.5:14b" not found'}))

    with pytest.raises(LLMError) as err:
        await p.complete([Message.user("hi")])

    assert err.value.code == "not_configured"
    assert "ollama pull qwen2.5:14b" in err.value.message


async def test_a_dead_daemon_is_not_configured_not_an_outage() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    p = make_provider(refuse)
    with pytest.raises(LLMError) as err:
        await p.complete([Message.user("hi")])

    assert err.value.code == "not_configured"
    assert "ollama serve" in err.value.message


async def test_rate_limit_and_auth_map_to_their_own_codes() -> None:
    p = make_provider(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(LLMError) as err:
        await p.complete([Message.user("hi")])
    assert err.value.code == "rate_limited"

    p = make_provider(lambda r: httpx.Response(401, text="unauthorized"))
    with pytest.raises(LLMError) as err:
        await p.complete([Message.user("hi")])
    assert err.value.code == "not_configured"
    assert "OLLAMA_API_KEY" in err.value.message


async def test_an_empty_reply_is_an_error_not_an_empty_lesson() -> None:
    p = make_provider(lambda r: chat_reply("   "))
    with pytest.raises(LLMError) as err:
        await p.complete([Message.user("hi")])
    assert err.value.code == "empty_completion"


async def test_an_error_mid_stream_surfaces() -> None:
    lines = [
        json.dumps({"message": {"content": "starting"}, "done": False}),
        json.dumps({"error": "out of memory"}),
    ]
    p = make_provider(lambda r: httpx.Response(200, text="\n".join(lines)))

    seen = []
    with pytest.raises(LLMError) as err:
        async for c in p.stream([Message.user("hi")]):
            seen.append(c)

    assert seen == ["starting"], "whatever arrived before the failure is still delivered"
    assert "out of memory" in err.value.message


# --- health ---------------------------------------------------------------


def tags_reply(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


async def test_probe_reports_ready_when_every_model_is_pulled() -> None:
    p = make_provider(lambda r: tags_reply("llama3.2:3b", "qwen2.5:14b", "qwen2.5:32b"))

    out = await p.probe()

    assert out["available"] is True
    assert out["reachable"] is True
    assert out["missingModels"] == []


async def test_probe_names_what_is_missing() -> None:
    p = make_provider(lambda r: tags_reply("llama3.2:3b"))

    out = await p.probe()

    assert out["available"] is False
    assert "qwen2.5:14b" in out["missingModels"]
    assert "ollama pull" in out["detail"]


async def test_probe_accepts_a_bare_name_as_the_latest_tag() -> None:
    p = make_provider(
        lambda r: tags_reply("mistral:latest"),
        ollama_model_quick="mistral",
        ollama_model_default="mistral",
        ollama_model_complex="mistral",
    )
    assert (await p.probe())["available"] is True


async def test_probe_never_raises_when_the_daemon_is_down() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    out = await make_provider(refuse).probe()

    assert out["available"] is False
    assert out["reachable"] is False
    assert "ollama serve" in out["detail"]


# --- configuration --------------------------------------------------------


def test_hosted_mode_is_detected_from_the_host() -> None:
    local = Settings(_env_file=None, llm_provider="ollama")
    hosted = Settings(_env_file=None, llm_provider="ollama", ollama_host="https://ollama.com")

    assert OllamaProvider(local).describe()["mode"] == "local"
    assert OllamaProvider(hosted).describe()["mode"] == "hosted"


def test_the_provider_is_registered() -> None:
    from syllabus_studio.llm import available_providers, get_provider

    assert "ollama" in available_providers()
    assert get_provider(Settings(_env_file=None, llm_provider="ollama")).name == "ollama"


def test_tiers_fall_back_to_the_generic_models_for_other_providers() -> None:
    s = Settings(_env_file=None, llm_provider="anthropic")
    assert s.model_for_tier("default") == s.model_default
    assert s.model_for_tier("default", "ollama") == s.ollama_model_default
