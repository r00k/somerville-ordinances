from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .config import AppSettings


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    raw: Any | None = None


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> ProviderResponse:
        raise NotImplementedError


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str, base_url: str | None, timeout_seconds: float):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }

        # Compatibility across OpenAI model families.
        kwargs["max_tokens"] = max_tokens
        try:
            completion = self._client.chat.completions.create(**kwargs)
        except Exception:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            completion = self._client.chat.completions.create(**kwargs)

        content = completion.choices[0].message.content or ""
        return ProviderResponse(content=content, raw=completion)


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, timeout_seconds: float):
        from anthropic import Anthropic

        self.model = model
        self._client = Anthropic(api_key=api_key, timeout=timeout_seconds)

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> ProviderResponse:
        completion = self._client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        parts: list[str] = []
        for item in completion.content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return ProviderResponse(content="\n".join(parts), raw=completion)


def build_provider(settings: AppSettings) -> ModelProvider:
    provider = settings.model_provider

    if provider == "openai":
        if not settings.model_api_key:
            raise RuntimeError("MODEL_PROVIDER=openai but no API key was provided (set MODEL_API_KEY or OPENAI_API_KEY).")
        return OpenAIProvider(
            model=settings.model_name,
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            timeout_seconds=settings.request_timeout_seconds,
        )

    if provider == "anthropic":
        if not settings.model_api_key:
            raise RuntimeError(
                "MODEL_PROVIDER=anthropic but no API key was provided (set MODEL_API_KEY or ANTHROPIC_API_KEY)."
            )
        return AnthropicProvider(
            model=settings.model_name,
            api_key=settings.model_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )

    raise RuntimeError(
        f"Unsupported MODEL_PROVIDER={provider!r}. Supported providers: openai, anthropic."
    )
