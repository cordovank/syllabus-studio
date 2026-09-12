"""Provider registry — name -> factory.

    from syllabus_studio.llm import get_provider
    provider = get_provider()           # honours SS_LLM_PROVIDER

To add one: subclass :class:`BaseProvider`, decorate with ``@register("name")``,
and import the module in ``providers/__init__.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from syllabus_studio.config import Settings, get_settings

from .base import BaseProvider

if TYPE_CHECKING:  # pragma: no cover
    pass

ProviderFactory = Callable[[Settings], BaseProvider]

_REGISTRY: dict[str, ProviderFactory] = {}


def register(name: str) -> Callable[[type[BaseProvider]], type[BaseProvider]]:
    def wrap(cls: type[BaseProvider]) -> type[BaseProvider]:
        cls.name = name
        _REGISTRY[name] = lambda settings: cls(settings)  # type: ignore[call-arg]
        return cls

    return wrap


def _load_builtins() -> None:
    """Importing the package runs the ``@register`` decorators."""
    from . import providers  # noqa: F401


def available_providers() -> list[str]:
    _load_builtins()
    return sorted(_REGISTRY)


def get_provider(settings: Settings | None = None, role: str | None = None) -> BaseProvider:
    """``role=None`` uses ``llm_provider`` directly; ``"author"``/``"reader"`` honour
    the per-role override and fall back to it."""
    _load_builtins()

    settings = settings or get_settings()
    name = settings.provider_for_role(role) if role else settings.llm_provider
    if name not in _REGISTRY:
        known = ", ".join(available_providers())
        var = f"SS_{role.upper()}_PROVIDER or SS_LLM_PROVIDER" if role else "SS_LLM_PROVIDER"
        raise ValueError(
            f"Unknown LLM provider {name!r}. Set {var} in your .env to one of: {known}."
        )
    return _REGISTRY[name](settings)
