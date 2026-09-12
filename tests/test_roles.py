"""Author and reader roles: which provider serves which job."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from syllabus_studio.config import Settings
from syllabus_studio.llm import LLMError, Message, get_provider


def _settings(**overrides) -> Settings:
    # Explicit blanks so a developer's .env or shell can't leak a role override in.
    base = {"llm_provider": "echo", "author_provider": "", "reader_provider": ""}
    return Settings(_env_file=None, **(base | overrides))


def test_provider_for_role_falls_back_to_llm_provider_when_unset() -> None:
    s = _settings(llm_provider="ollama")
    assert s.provider_for_role("author") == "ollama"
    assert s.provider_for_role("reader") == "ollama"
    assert get_provider(s).name == get_provider(s, role="reader").name == "ollama"


def test_an_unknown_role_is_rejected_rather_than_silently_defaulted() -> None:
    with pytest.raises(ValueError):
        _settings().provider_for_role("editor")


def test_author_and_reader_resolve_to_different_classes_when_both_are_set() -> None:
    s = _settings(author_provider="anthropic", reader_provider="ollama")
    author = get_provider(s, role="author")
    reader = get_provider(s, role="reader")
    assert type(author) is not type(reader)
    assert (author.name, reader.name) == ("anthropic", "ollama")
    # role=None still means llm_provider, so existing call sites don't move
    assert get_provider(s).name == "echo"


def test_the_none_provider_reports_unavailable() -> None:
    provider = get_provider(_settings(llm_provider="none"))
    assert provider.describe() == {"provider": "none", "available": False}


async def test_the_none_provider_raises_not_configured_on_every_call() -> None:
    provider = get_provider(_settings(llm_provider="none"))
    turns = [Message.user("hello")]

    with pytest.raises(LLMError) as complete:
        await provider.complete(turns)
    assert complete.value.code == "not_configured"

    with pytest.raises(LLMError) as structured:
        await provider.complete_json(turns)
    assert structured.value.code == "not_configured"

    with pytest.raises(LLMError) as streamed:
        async for _ in provider.stream(turns):
            pass
    assert streamed.value.code == "not_configured"


def test_routes_spend_the_model_of_the_role_they_serve(tmp_path: Path, syllabus: str) -> None:
    from fastapi.testclient import TestClient

    from syllabus_studio.app import create_app

    s = _settings(reader_provider="none", db_path=tmp_path / "t.db", seed_demo_course=False)
    with TestClient(create_app(s)) as client:
        # authoring runs on the author's echo model
        course = client.post("/api/v1/courses", json={"syllabus": syllabus}).json()
        cid, lid = course["id"], course["modules"][0]["lessons"][0]["id"]
        assert client.post(f"/api/v1/courses/{cid}/lessons/{lid}/generate").status_code == 201

        # tutoring runs on the reader's, which is none
        res = client.post(
            f"/api/v1/courses/{cid}/lessons/{lid}/ask",
            json={"turns": [{"role": "user", "content": "why?"}]},
        )
        assert res.status_code == 503
        assert res.json()["code"] == "not_configured"


def test_env_file_values_reach_os_environ_but_the_real_environment_wins(
    tmp_path: Path, monkeypatch
) -> None:
    from syllabus_studio.config import load_env_file

    env = tmp_path / ".env"
    env.write_text("SS_TEST_ONLY_FROM_FILE=file\nSS_TEST_SET_IN_SHELL=file\n", encoding="utf-8")
    monkeypatch.delenv("SS_TEST_ONLY_FROM_FILE", raising=False)
    monkeypatch.setenv("SS_TEST_SET_IN_SHELL", "shell")

    assert load_env_file(env) is True

    assert os.environ["SS_TEST_ONLY_FROM_FILE"] == "file", "libraries reading os.environ see it"
    assert os.environ["SS_TEST_SET_IN_SHELL"] == "shell", "an explicit variable beats the file"
    monkeypatch.delenv("SS_TEST_ONLY_FROM_FILE")
