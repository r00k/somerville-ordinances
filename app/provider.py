from __future__ import annotations

import re
from abc import ABC, abstractmethod

from .types import CorpusName, CorpusSection


class CorpusModel(ABC):
    model_name: str
    corpus_context_chars: int

    @abstractmethod
    def generate(self, *, question: str, history: list[dict[str, str]]) -> str:
        raise NotImplementedError


class OpenAICorpusModel(CorpusModel):
    def __init__(
        self,
        *,
        corpus: CorpusName,
        model_name: str,
        api_key: str,
        timeout_seconds: float,
        sections: list[CorpusSection],
    ):
        from openai import OpenAI

        self.corpus = corpus
        self.model_name = model_name
        self.section_count = len(sections)
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self._corpus_context = self._build_corpus_context(sections)
        self.corpus_context_chars = len(self._corpus_context)

    @staticmethod
    def _build_corpus_context(sections: list[CorpusSection]) -> str:
        lines: list[str] = []
        for section in sections:
            heading_path = " > ".join(section.heading_path) if section.heading_path else section.heading
            lines.append(f"[SECTION]")
            lines.append(f"corpus: {section.corpus}")
            lines.append(f"secid: {section.secid}")
            lines.append(f"heading: {heading_path}")
            lines.append("text:")
            lines.append(section.text)
            lines.append("[END SECTION]")
            lines.append("")
        return "\n".join(lines).strip()

    def generate(self, *, question: str, history: list[dict[str, str]]) -> str:
        system_prompt = (
            "You are a QA assistant for Somerville municipal law. "
            "Use ONLY the corpus sections provided below. "
            "Answer as fully as you can from the available sections, even if they don't cover every aspect of the topic. "
            "Only set insufficient_context=true if the sections contain nothing relevant at all. "
            "Do not invent section IDs, percentages, or permissions. "
            "Each citation quote MUST be copied exactly from the section text (exact substring match). "
            "Do NOT include any disclaimers, legal warnings, or 'informational only' notices in your answer. "
            "Return strictly valid JSON matching the requested schema.\n\n"
            "Corpus sections:\n"
            f"{self._corpus_context}"
        )

        history_lines: list[str] = []
        for item in history:
            role = item.get("role", "user")
            content = item.get("content", "").strip()
            if content:
                history_lines.append(f"- {role}: {content}")
        history_block = "\n".join(history_lines) if history_lines else "(no prior messages)"

        user_prompt = (
            "Return JSON with this schema exactly:\n"
            '{"answer_markdown": string, "citations": [{"corpus": "non_zoning|zoning", "secid": string, '
            '"quote": string, "reason": string}], "confidence": "low|medium|high", '
            '"insufficient_context": boolean, "clarification_question": string|null}\n\n'
            "Rules:\n"
            "1) Only answer using the corpus sections in the system prompt.\n"
            "2) Every material claim must be supported by at least one citation.\n"
            "3) citation.quote must be an exact excerpt from that section text.\n"
            "4) If uncertain or incomplete, set insufficient_context=true.\n"
            "5) Keep answer concise and include numbers/conditions directly when present.\n\n"
            "Conversation history:\n"
            f"{history_block}\n\n"
            "Question:\n"
            f"{question}"
        )

        completion = self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
        return completion.choices[0].message.content or ""
