"""Anthropic Agent SDK-based answer engine.

Uses tool_runner with search_toc and get_section tools to let the model
autonomously navigate the Somerville law corpus and answer questions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from anthropic import AsyncAnthropic
from anthropic.lib.tools import ToolError, beta_async_tool
from pydantic import BaseModel, Field, ValidationError

from .config import AppSettings
from .observability import log_event, serialize_exception
from .toc import CorpusToc
from .types import Confidence


SYSTEM_PROMPT = """\
You are a QA assistant for Somerville, MA municipal law.

Tool-use strategy (be efficient — each tool call adds latency):
1. Call search_toc ONCE to find relevant chapters.
2. Call get_section for each chapter you need — fetch all needed sections before answering.
3. Do NOT search again unless the first search returned nothing relevant.

Answering rules:
- Answer using ONLY the retrieved law text.
- Every material claim must be supported by an exact quote.
- Do NOT invent information not present in the retrieved text.
- Do NOT include any disclaimers, legal warnings, or "informational only" notices.
- Use bold sparingly — only for the single most important fact, if any.
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


@dataclass
class _RequestToolState:
    """Tracks which chapters were retrieved during a single request."""
    requested_indices: list[int] = field(default_factory=list)

    def record(self, idx: int) -> None:
        if idx not in self.requested_indices:
            self.requested_indices.append(idx)


class SomervilleLawAgent:
    def __init__(self, *, settings: AppSettings, toc: CorpusToc, client: AsyncAnthropic):
        self.settings = settings
        self.toc = toc
        self.client = client

    def _build_tools(self, *, state: _RequestToolState, request_id: str | None):
        toc = self.toc
        search_limit = self.settings.toc_search_limit

        @beta_async_tool
        async def search_toc(query: str) -> str:
            """Search the Somerville law table of contents by keyword.

            Use this before get_section to find which chapters are relevant.

            Args:
                query: Natural-language topic or keywords to search for.
            """
            hits = toc.search(query, limit=search_limit)
            payload = [
                {
                    "chapter_index": hit.chapter_index,
                    "corpus": hit.corpus,
                    "heading": hit.heading,
                    "heading_path": list(hit.heading_path),
                    "subheadings": list(hit.subheadings[:12]),
                }
                for hit in hits
            ]
            log_event(
                "agent.tool.search_toc",
                request_id=request_id,
                query=query,
                match_count=len(payload),
            )
            return json.dumps(payload, ensure_ascii=False)

        @beta_async_tool
        async def get_section(chapter_index: int) -> str:
            """Retrieve the full text of a Somerville law chapter by its index.

            Args:
                chapter_index: The numeric chapter index returned by search_toc.
            """
            try:
                chapter = toc.chapter_at(chapter_index)
            except IndexError:
                raise ToolError(f"Invalid chapter_index: {chapter_index}. Use search_toc to find valid indices.")

            state.record(chapter_index)
            text = toc.chapter_text(chapter)

            log_event(
                "agent.tool.get_section",
                request_id=request_id,
                chapter_index=chapter_index,
                heading=chapter.heading,
                text_length=len(text),
            )

            return json.dumps(
                {
                    "chapter_index": chapter_index,
                    "corpus": chapter.corpus,
                    "heading": chapter.heading,
                    "heading_path": list(chapter.heading_path),
                    "source_heading": " > ".join(chapter.heading_path),
                    "text": text,
                },
                ensure_ascii=False,
            )

        return [search_toc, get_section]

    async def ask(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        request_id: str | None = None,
    ) -> AgentResult:
        state = _RequestToolState()
        tools = self._build_tools(state=state, request_id=request_id)

        messages: list[dict[str, str]] = [
            {"role": item["role"], "content": item["content"]}
            for item in history
            if item.get("content", "").strip()
        ]
        messages.append({"role": "user", "content": question})

        log_event(
            "agent.started",
            request_id=request_id,
            question=question,
            history_count=len(history),
            model=self.settings.model_name,
        )

        runner = self.client.beta.messages.tool_runner(
            model=self.settings.model_name,
            max_tokens=self.settings.max_output_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
            temperature=0.0,
        )

        final_message = None
        async for message in runner:
            final_message = message

        if final_message is None:
            raise RuntimeError("Agent runner returned no final message.")

        text_blocks = [
            block.text
            for block in final_message.content
            if getattr(block, "type", None) == "text"
        ]
        raw_text = "\n".join(text_blocks).strip()

        log_event(
            "agent.raw_response",
            request_id=request_id,
            question=question,
            raw_response=raw_text,
            tool_calls=len(state.requested_indices),
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
                selected_chapters=[self.toc.chapter_at(i).heading for i in state.requested_indices],
            )

        selected = [self.toc.chapter_at(i).heading for i in state.requested_indices]

        log_event(
            "agent.completed",
            request_id=request_id,
            question=question,
            confidence=payload.confidence,
            citation_count=len(payload.citations),
            selected_chapters=selected,
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
            selected_chapters=selected,
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
