from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from .base import BaseLLM


@dataclass
class ProviderSpec:
    name: str
    display_name: str

    module: str
    class_name: str

    dependency: str | None = None


class LLMProviderRegistry:

    def __init__(self):
        self._providers: dict[str, ProviderSpec] = {}

    def register(
        self,
        provider: ProviderSpec,
    ) -> None:
        self._providers[provider.name] = provider

    def unregister(
        self,
        name: str,
    ) -> None:
        self._providers.pop(name, None)

    def get(
        self,
        name: str,
    ) -> ProviderSpec | None:
        return self._providers.get(name)

    def all(self) -> list[ProviderSpec]:
        return list(self._providers.values())

    def is_installed(
        self,
        name: str,
    ) -> bool:

        provider = self._providers.get(name)

        if provider is None:
            return False

        if provider.dependency is None:
            return True

        return find_spec(provider.dependency) is not None

    def available(self) -> list[ProviderSpec]:

        return [
            provider
            for provider in self._providers.values()
            if self.is_installed(provider.name)
        ]

    def create(
        self,
        name: str,
        **kwargs: Any,
    ) -> BaseLLM:

        provider = self._providers.get(name)

        if provider is None:
            raise ValueError(
                f"Unknown LLM provider: {name}"
            )

        if not self.is_installed(name):
            raise RuntimeError(
                f"{provider.display_name} provider dependency "
                f"is not installed."
            )

        module = import_module(provider.module)

        provider_class = getattr(
            module,
            provider.class_name,
        )

        return provider_class(**kwargs)
