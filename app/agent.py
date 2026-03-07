"""Single-call answer engine.

Does TOC search and section retrieval programmatically, then sends
a single Anthropic API call with the context already included.
This eliminates the 2+ extra round-trips from tool-use loops.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Optional

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field, ValidationError

from .config import AppSettings
from .observability import log_event, serialize_exception
from .toc import CorpusToc
from .types import Confidence


SYSTEM_PROMPT = """\
You are a QA assistant for Somerville, MA municipal law.

You will be given retrieved sections of Somerville law relevant to the user's question.

Answering rules:
- Answer using ONLY the retrieved law text provided below.
- Every material claim must be supported by an exact quote.
- Do NOT invent information not present in the retrieved text.
- Do NOT include any disclaimers, legal warnings, or "informational only" notices.
- Use bold sparingly — only for the single most important fact, if any.
- Be concise. Summarize rather than listing every detail. Keep citations to 1–3 key quotes.
- If the retrieved text is insufficient, ask a clarification question.

YOUR FINAL RESPONSE MUST BE EXACTLY ONE JSON OBJECT — no prose, no markdown fences, no explanation before or after.
Shape: {"answer_markdown": string, "citations": [{"quote": string, "source_heading": string, "reason": string}], \
"confidence": "low"|"medium"|"high", "clarification_question": string|null}

The JSON must be valid. Escape double quotes inside string values with backslash. \
Do NOT return plain text — always return the JSON object.\
"""


class CitationPayload(BaseModel):
    quote: str
    source_heading: str
    reason: str = ""


class AgentAnswerPayload(BaseModel):
    answer_markdown: str
    citations: list[CitationPayload] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    clarification_question: Optional[str] = None


@dataclass(frozen=True)
class AgentCitation:
    quote: str
    source_heading: str
    reason: str


@dataclass(frozen=True)
class AgentResult:
    answer: str
    citations: list[AgentCitation]
    confidence: Confidence
    needs_clarification: bool
    clarification_question: str | None
    selected_chapters: list[str]


class SomervilleLawAgent:
    def __init__(self, *, settings: AppSettings, toc: CorpusToc, client: AsyncAnthropic):
        self.settings = settings
        self.toc = toc
        self.client = client

    def _retrieve_context(
        self,
        question: str,
        history: list[dict[str, str]],
        *,
        request_id: str | None,
    ) -> tuple[str, list[str]]:
        """Search TOC and retrieve relevant chapter text. Returns (context_block, selected_headings)."""
        # Build a richer query from the question + recent history for better recall
        query_parts = [question]
        for item in history[-4:]:
            if item["role"] == "user":
                query_parts.append(item["content"])
        search_query = " ".join(query_parts)
        hits = self.toc.search(search_query, limit=self.settings.toc_search_limit)

        log_event(
            "retrieval.search",
            request_id=request_id,
            query=question,
            match_count=len(hits),
        )

        # Take top 3 hits by score
        top_hits = hits[:3]
        sections: list[str] = []
        selected_headings: list[str] = []

        for hit in top_hits:
            chapter = self.toc.chapter_at(hit.chapter_index)
            text = self.toc.chapter_text(chapter)
            source_heading = " > ".join(chapter.heading_path)
            sections.append(f"--- Section: {source_heading} ---\n{text}")
            selected_headings.append(chapter.heading)

            log_event(
                "retrieval.section",
                request_id=request_id,
                chapter_index=hit.chapter_index,
                heading=chapter.heading,
                text_length=len(text),
            )

        context_block = "\n\n".join(sections) if sections else "(No relevant sections found.)"
        return context_block, selected_headings

    async def ask(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        request_id: str | None = None,
    ) -> AgentResult:
        context_block, selected_headings = self._retrieve_context(question, history, request_id=request_id)

        messages: list[dict[str, str]] = [
            {"role": item["role"], "content": item["content"]}
            for item in history
            if item.get("content", "").strip()
        ]

        user_content = f"""<retrieved_law_text>
{context_block}
</retrieved_law_text>

Question: {question}"""

        messages.append({"role": "user", "content": user_content})

        log_event(
            "agent.started",
            request_id=request_id,
            question=question,
            history_count=len(history),
            model=self.settings.model_name,
            selected_chapters=selected_headings,
        )

        response = await self.client.messages.create(
            model=self.settings.model_name,
            max_tokens=self.settings.max_output_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
            temperature=0.0,
        )

        text_blocks = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        raw_text = "\n".join(text_blocks).strip()

        log_event(
            "agent.raw_response",
            request_id=request_id,
            question=question,
            raw_response=raw_text,
        )

        try:
            payload = _parse_answer_payload(raw_text)
        except Exception as exc:
            log_event(
                "agent.parse_failed",
                level="error",
                request_id=request_id,
                question=question,
                raw_response=raw_text,
                error=serialize_exception(exc),
            )
            return AgentResult(
                answer="I encountered an error processing the law text. Please try rephrasing your question.",
                citations=[],
                confidence="low",
                needs_clarification=True,
                clarification_question="Could you rephrase your question?",
                selected_chapters=selected_headings,
            )

        log_event(
            "agent.completed",
            request_id=request_id,
            question=question,
            confidence=payload.confidence,
            citation_count=len(payload.citations),
            selected_chapters=selected_headings,
        )

        return AgentResult(
            answer=payload.answer_markdown.strip(),
            citations=[
                AgentCitation(
                    quote=c.quote.strip(),
                    source_heading=c.source_heading.strip(),
                    reason=c.reason.strip(),
                )
                for c in payload.citations
            ],
            confidence=payload.confidence,
            needs_clarification=bool(payload.clarification_question),
            clarification_question=payload.clarification_question,
            selected_chapters=selected_headings,
        )


def _extract_json(text: str) -> str:
    text = text.strip()
    # Strip markdown code fences (```json ... ```)
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError("No JSON object found in model response.")
    return match.group(0)


def _parse_answer_payload(raw: str) -> AgentAnswerPayload:
    candidate = _extract_json(raw)
    try:
        return AgentAnswerPayload.model_validate_json(candidate)
    except ValidationError as exc:
        raise RuntimeError(f"Agent response schema validation failed: {exc}") from exc
