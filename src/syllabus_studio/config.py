"""Application settings.

Everything is read from the environment (or a local ``.env``) with a working
default, so ``python -m syllabus_studio`` starts even on a clean checkout with
no keys configured — it just falls back to the offline ``echo`` provider.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SS_",
        extra="ignore",
    )

    # --- llm ---------------------------------------------------------------
    llm_provider: str = "echo"
    """Registered provider name: anthropic | ollama | echo."""

    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "SS_ANTHROPIC_API_KEY"),
    )
    """Read from the bare ANTHROPIC_API_KEY, because that is what everything else
    in the ecosystem uses. Note that pydantic-settings reads .env for its own
    fields only -- it never exports them to os.environ -- so any value the app
    needs from .env has to be declared here."""

    model_quick: str = "claude-haiku-4-5"
    model_default: str = "claude-sonnet-4-5"
    model_complex: str = "claude-opus-4-5"

    max_output_tokens: int = 8000
    request_timeout_s: float = 300.0

    # Ollama ----------------------------------------------------------------
    ollama_host: str = "http://localhost:11434"
    """The local daemon, or https://ollama.com to reach Ollama's hosted models
    directly. Either way the wire protocol is the same."""

    ollama_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OLLAMA_API_KEY", "SS_OLLAMA_API_KEY"),
    )
    """Only needed when talking to a hosted endpoint. A local daemon needs nothing,
    including for cloud models, once you have run `ollama signin`."""

    ollama_model_quick: str = "llama3.2:3b"
    ollama_model_default: str = "qwen2.5:14b"
    ollama_model_complex: str = "qwen2.5:32b"

    ollama_keep_alive: str = "5m"
    """How long Ollama holds the model in memory after a call. "0" unloads
    immediately; "-1" keeps it loaded. Matters when RAM is tight."""

    # --- storage -----------------------------------------------------------
    storage_backend: str = "sqlite"
    """sqlite | remote."""

    db_path: Path = REPO_ROOT / "data" / "syllabus_studio.db"
    remote_url: str = ""
    remote_token: str = ""

    seed_demo_course: bool = True

    # --- catalog -----------------------------------------------------------
    catalog_url: str = ""
    """URL of a JSON index of shareable course bundles. Empty = local only."""

    # --- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = False
    cors_origins: list[str] = []

    def model_for_tier(self, tier: str, provider: str | None = None) -> str:
        """Which concrete model answers a given tier, for the active provider.

        Each provider may declare its own ``<name>_model_<tier>`` settings; when
        one is blank the generic ``model_<tier>`` is used. That keeps a single
        tier vocabulary (quick / default / complex) across very different
        backends without the calling code ever naming a model.
        """
        tier = tier if tier in ("quick", "default", "complex") else "default"
        name = provider or self.llm_provider

        specific = getattr(self, f"{name}_model_{tier}", "")
        if specific:
            return specific

        return {
            "quick": self.model_quick,
            "default": self.model_default,
            "complex": self.model_complex,
        }[tier]

    def models_for(self, provider: str | None = None) -> dict[str, str]:
        return {t: self.model_for_tier(t, provider) for t in ("quick", "default", "complex")}


@lru_cache
def get_settings() -> Settings:
    return Settings()
