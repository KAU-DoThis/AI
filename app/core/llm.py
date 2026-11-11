"""Build existing LLM providers from server configuration without network I/O."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.modules.llm import BaseLLM, LLMProviderRegistry, ProviderSpec


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 10.0
    max_tokens: int = 512
    temperature: float = 0.4
    output_mode: str = "json_schema"

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings:
        env = os.environ if environ is None else environ
        provider = env.get("LLM_PROVIDER", "openai").strip().lower()
        if provider not in {"openai", "ollama"}:
            raise ValueError("LLM_PROVIDER must be openai or ollama")
        output_mode = env.get("LLM_OUTPUT_MODE", "json_schema").strip().lower()
        if output_mode not in {"json_schema", "json_object", "none"}:
            raise ValueError("LLM_OUTPUT_MODE must be json_schema, json_object, or none")

        base_url = env.get("LLM_BASE_URL", "").strip().rstrip("/")
        if base_url:
            try:
                parsed = urlsplit(base_url)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and not parsed.query
                    and not parsed.fragment
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("LLM_BASE_URL must be an HTTP(S) base URL without query or fragment")

        timeout_seconds = _float_setting(env, "LLM_TIMEOUT_SECONDS", "10")
        if timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be greater than zero")
        temperature = _float_setting(env, "LLM_TEMPERATURE", "0.4")
        if not 0 <= temperature <= 2:
            raise ValueError("LLM_TEMPERATURE must be between 0 and 2")
        try:
            max_tokens = int(env.get("LLM_MAX_TOKENS", "512"))
        except (TypeError, ValueError):
            raise ValueError("LLM_MAX_TOKENS must be a positive integer") from None
        if max_tokens <= 0:
            raise ValueError("LLM_MAX_TOKENS must be a positive integer")

        return cls(
            provider=provider,
            base_url=base_url,
            model=env.get("LLM_MODEL", "").strip(),
            api_key=env.get("LLM_API_KEY", ""),
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            temperature=temperature,
            output_mode=output_mode,
        )


def _float_setting(env: Mapping[str, str], name: str, default: str) -> float:
    try:
        value = float(env.get(name, default))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def create_llm(settings: LLMSettings) -> BaseLLM | None:
    """Create a provider, or disable room generation until URL and model are set."""
    if not settings.is_configured:
        return None
    registry = LLMProviderRegistry()
    registry.register(ProviderSpec(
        name="openai", display_name="OpenAI Compatible",
        module="app.modules.llm.provider.openai", class_name="OpenAILLM",
        dependency="openai",
    ))
    registry.register(ProviderSpec(
        name="ollama", display_name="Ollama",
        module="app.modules.llm.provider.ollama", class_name="OllamaLLM",
        dependency="ollama",
    ))
    if settings.provider == "openai":
        return registry.create(
            "openai", base_url=settings.base_url,
            api_key=settings.api_key or "EMPTY",
            timeout=settings.timeout_seconds, max_retries=0,
        )
    if settings.provider == "ollama":
        return registry.create("ollama", host=settings.base_url, timeout=settings.timeout_seconds)
    raise ValueError("LLM_PROVIDER must be openai or ollama")
