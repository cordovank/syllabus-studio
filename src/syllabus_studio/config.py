"""Application settings.

Everything is read from the environment with a working default, so
``python -m syllabus_studio`` starts even on a clean checkout with no keys
configured — it just falls back to the offline ``echo`` provider.

A local ``.env`` is loaded into the environment by python-dotenv when this
module is imported, and ``Settings`` reads only the environment. One loading
path: the app, the tests and any library reading ``os.environ`` directly (the
Anthropic SDK looks for ANTHROPIC_API_KEY) all see the same values.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent


def load_env_file(path: Path | str | None = None) -> bool:
    """Export ``.env`` into ``os.environ``. Returns whether a file was loaded.

    Looks upward from the working directory, then falls back to the repo root.
    ``override=False``: a variable already set in the real environment wins over
    the file, so ``SS_LLM_PROVIDER=echo make test`` means what it says.
    """
    target = path or find_dotenv(usecwd=True) or REPO_ROOT / ".env"
    return load_dotenv(target, override=False, encoding="utf-8")


load_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SS_",
        extra="ignore",
    )

    # --- llm ---------------------------------------------------------------
    llm_provider: str = "echo"
    """Registered provider name: anthropic | ollama | echo | none."""

    author_provider: str = "ollama"
    """Provider for authoring (outlines, lessons). Empty = same as ``llm_provider``."""

    reader_provider: str = "none"
    """Provider for read-time tutoring (live lenses, ask). Empty = same as
    ``llm_provider``. Split from the author so a reader can run a small local
    model, or ``none``, against a course a strong model wrote."""

    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "SS_ANTHROPIC_API_KEY"),
    )
    """Read from the bare ANTHROPIC_API_KEY, because that is what everything else
    in the ecosystem uses. It reaches the environment from .env via
    ``load_env_file`` above."""

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

    ollama_model_quick: str = "qwen3.6:27b"
    ollama_model_default: str = "gpt-oss:latest"
    ollama_model_complex: str = "qwen3.8:latest"

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

    # --- site --------------------------------------------------------------
    site_dir: Path = REPO_ROOT / "site"
    """Where ``syllabus-studio site build`` writes the published reader (spec 003)."""

    # --- catalog -----------------------------------------------------------
    catalog_url: str = ""
    """URL of a JSON index of shareable course bundles. Empty = local only."""

    # --- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True
    cors_origins: list[str] = []

    def provider_for_role(self, role: str) -> str:
        """role: "author" | "reader". Empty override means "same as llm_provider"."""
        if role not in ("author", "reader"):
            raise ValueError(f"Unknown provider role {role!r}; expected 'author' or 'reader'.")
        override = self.author_provider if role == "author" else self.reader_provider
        return override or self.llm_provider

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
