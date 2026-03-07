from __future__ import annotations

import json
import math
import re
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


class MockProvider(ModelProvider):
    """Deterministic local provider for smoke tests and offline development."""

    name = "mock"

    SECTION_RE = re.compile(
        r"\[SECTION\s+(S\d+)\]\n"
        r"corpus:\s*(?P<corpus>[^\n]+)\n"
        r"secid:\s*(?P<secid>[^\n]+)\n"
        r"heading:\s*(?P<heading>[^\n]+)\n"
        r"text:\n(?P<text>.*?)\n\[END SECTION\]",
        flags=re.DOTALL,
    )

    def __init__(self, model: str = "mock-local"):
        self.model = model

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> ProviderResponse:
        del system_prompt, temperature, max_tokens
        question = self._extract_question(user_prompt)
        sections = list(self._extract_sections(user_prompt))
        payload = self._answer(question, sections)
        return ProviderResponse(content=json.dumps(payload))

    def _extract_question(self, user_prompt: str) -> str:
        match = re.search(r"Question:\s*(.+?)\n\nRetrieved sections:", user_prompt, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_sections(self, user_prompt: str):
        for match in self.SECTION_RE.finditer(user_prompt):
            yield {
                "label": match.group(1),
                "corpus": match.group("corpus").strip(),
                "secid": match.group("secid").strip(),
                "heading": match.group("heading").strip(),
                "text": match.group("text").strip(),
            }

    def _answer(self, question: str, sections: list[dict[str, str]]) -> dict[str, Any]:
        q = question.lower()

        if "city council" in q and ("how many" in q or "number" in q):
            for section in sections:
                match = re.search(
                    r"city council consisting of\s+(\d+)\s+members",
                    section["text"],
                    flags=re.IGNORECASE,
                )
                if match:
                    count = int(match.group(1))
                    quote = self._quote_around(section["text"], match.start(), match.end())
                    return self._payload(
                        answer=(
                            f"Somerville's city council has **{count} members**. "
                            "That is four councilors at-large and seven ward councilors."
                        ),
                        citations=[self._citation(section, quote, "Defines city council composition")],
                        confidence="high",
                    )

        if "affordable" in q and "unit" in q and "20" in q and "2" in q:
            joined = "\n\n".join(section["text"] for section in sections)
            pct_two = self._find_percent_for_units(joined, 2)
            pct_twenty = self._find_percent_for_units(joined, 20)
            if pct_two is None:
                pct_two = 0
            if pct_twenty is None:
                pct_twenty = 20

            twenty_units_required = math.floor((20 * pct_twenty) / 100)

            citation_section = sections[0] if sections else None
            quote = (
                self._best_affordable_quote(citation_section["text"]) if citation_section else ""
            )
            citations = [
                self._citation(citation_section, quote, "Inclusionary housing percentages")
            ] if citation_section and quote else []

            return self._payload(
                answer=(
                    "For inclusionary zoning obligations based on the retrieved Somerville zoning text: "
                    f"a project with **2 units requires 0 affordable units** (0%), and "
                    f"a project with **20 units requires {twenty_units_required} affordable units** ({pct_twenty}%)."
                ),
                citations=citations,
                confidence="medium" if citations else "low",
                insufficient_context=not bool(citations),
            )

        if "demolish" in q and ("100 year" in q or "100-year" in q):
            section = sections[0] if sections else None
            quote = ""
            if section:
                quote_match = re.search(
                    r"(?:must|shall)\s+obtain[^.]{0,220}permit",
                    section["text"],
                    flags=re.IGNORECASE,
                )
                if quote_match:
                    quote = self._quote_around(section["text"], quote_match.start(), quote_match.end())
                else:
                    quote = section["text"][:240]
            citations = [self._citation(section, quote, "Demolition permission requirement")] if section else []
            return self._payload(
                answer=(
                    "No. A 100-year-old building cannot be demolished without required City permission. "
                    "Demolition review and permitting requirements apply."
                ),
                citations=citations,
                confidence="medium" if citations else "low",
                insufficient_context=not bool(citations),
            )

        # Conservative default for unknown prompts.
        return self._payload(
            answer="I do not have enough grounded context to answer this reliably.",
            citations=[],
            confidence="low",
            insufficient_context=True,
            clarification_question="Could you narrow the question to a specific ordinance topic or section?",
        )

    @staticmethod
    def _quote_around(text: str, start: int, end: int, *, width: int = 220) -> str:
        left = max(0, start - width // 2)
        right = min(len(text), end + width // 2)
        excerpt = text[left:right]
        return re.sub(r"\s+", " ", excerpt).strip()

    @staticmethod
    def _find_percent_for_units(text: str, units: int) -> int | None:
        patterns = [
            rf"{units}\s*(?:dwelling\s+)?units?[^%\n]{{0,60}}?(\d+)\s*%",
            rf"{units}\s*(?:dwelling\s+)?units?[^\n]{{0,120}}?requires?[^\n]{{0,80}}?(\d+)\s*%",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _best_affordable_quote(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if "affordable" in line.lower() and "%" in line:
                return re.sub(r"\s+", " ", line)
        compact = re.sub(r"\s+", " ", text).strip()
        return compact[:240]

    @staticmethod
    def _citation(section: dict[str, str] | None, quote: str, reason: str) -> dict[str, str]:
        if not section:
            return {"corpus": "zoning", "secid": "unknown", "quote": quote, "reason": reason}
        return {
            "corpus": section["corpus"],
            "secid": section["secid"],
            "quote": quote,
            "reason": reason,
        }

    @staticmethod
    def _payload(
        *,
        answer: str,
        citations: list[dict[str, str]],
        confidence: str,
        insufficient_context: bool = False,
        clarification_question: str | None = None,
    ) -> dict[str, Any]:
        return {
            "answer_markdown": answer,
            "citations": citations,
            "confidence": confidence,
            "insufficient_context": insufficient_context,
            "clarification_question": clarification_question,
        }


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

    if provider == "mock":
        return MockProvider(model=settings.model_name)

    raise RuntimeError(
        f"Unsupported MODEL_PROVIDER={provider!r}. Supported providers: openai, anthropic, mock."
    )
