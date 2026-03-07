"""Two-pass LLM answer engine.

Pass 1: Show the LLM a table of contents and ask it to pick relevant chapters.
Pass 2: Send the full text of those chapters and ask it to answer the question.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from .config import AppSettings
from .observability import log_event, serialize_exception
from .provider import ModelProvider
from .toc import CorpusToc, TocChapter
from .types import Confidence


MAX_CONTEXT_BYTES = 600_000  # ~150k tokens budget for chapter text in pass 2


class TocSelection(BaseModel):
    """LLM output from pass 1: which chapters to retrieve."""

    chapter_indices: list[int] = Field(default_factory=list)
    reasoning: str = ""


class CitationPayload(BaseModel):
    quote: str
    source_heading: str
    reason: str = ""


class AnswerPayload(BaseModel):
    answer_markdown: str
    citations: list[CitationPayload] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    clarification_question: Optional[str] = None


@dataclass(frozen=True)
class TwoPassCitation:
    quote: str
    source_heading: str
    reason: str


@dataclass(frozen=True)
class TwoPassResult:
    answer: str
    citations: list[TwoPassCitation]
    confidence: Confidence
    needs_clarification: bool
    clarification_question: str | None
    selected_chapters: list[str]  # headings of chapters selected in pass 1


class TwoPassEngine:
    def __init__(
        self,
        *,
        settings: AppSettings,
        toc: CorpusToc,
        provider: ModelProvider,
        pass1_provider: ModelProvider | None = None,
    ):
        self.settings = settings
        self.toc = toc
        self.provider = provider
        self.pass1_provider = pass1_provider or provider

    def ask(
        self,
        question: str,
        history: list[dict[str, str]],
        request_id: str | None = None,
    ) -> TwoPassResult:
        log_event(
            "two_pass.started",
            request_id=request_id,
            question=question,
            history=history,
            provider=self.provider.name,
            model=self.settings.model_name,
        )

        # --- Pass 1: TOC selection ---
        selected_chapters = self._pass1_select_chapters(question, history, request_id)

        if not selected_chapters:
            log_event(
                "two_pass.no_chapters_selected",
                level="warning",
                request_id=request_id,
                question=question,
            )
            return TwoPassResult(
                answer="I couldn't identify which part of Somerville law is relevant to your question.",
                citations=[],
                confidence="low",
                needs_clarification=True,
                clarification_question="Could you rephrase or specify which area of city law you're asking about?",
                selected_chapters=[],
            )

        # --- Pass 2: Answer from full chapter text ---
        result = self._pass2_answer(question, history, selected_chapters, request_id)

        log_event(
            "two_pass.completed",
            request_id=request_id,
            question=question,
            confidence=result.confidence,
            citation_count=len(result.citations),
            selected_chapters=[ch.heading for ch in selected_chapters],
        )

        return result

    def _pass1_select_chapters(
        self,
        question: str,
        history: list[dict[str, str]],
        request_id: str | None = None,
    ) -> list[TocChapter]:
        toc_text = self.toc.render_toc()

        history_block = _format_history(history)

        system_prompt = (
            "You are an expert at navigating Somerville, MA municipal law.\n"
            "Given a user's question and a table of contents, select which chapters "
            "are most likely to contain the answer.\n"
            "Return JSON: {\"chapter_indices\": [int, ...], \"reasoning\": string}\n"
            "Select the FEWEST chapters needed (usually 1-3). Only select more if "
            "the question genuinely spans multiple topics.\n"
            "If the question is about a specific topic, select the most specific chapter."
        )

        user_prompt = (
            f"Conversation history:\n{history_block}\n\n"
            f"Question: {question}\n\n"
            f"Table of Contents:\n{toc_text}"
        )

        log_event(
            "two_pass.pass1_request",
            request_id=request_id,
            question=question,
            toc_length=len(toc_text),
            user_prompt_length=len(user_prompt),
        )

        raw = self.pass1_provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=500,
        ).content

        log_event(
            "two_pass.pass1_response",
            request_id=request_id,
            question=question,
            raw_response=raw,
        )

        try:
            selection = _parse_toc_selection(raw)
        except Exception as exc:
            log_event(
                "two_pass.pass1_parse_failed",
                level="error",
                request_id=request_id,
                question=question,
                error=serialize_exception(exc),
            )
            return []

        valid_indices = [
            i for i in selection.chapter_indices
            if 0 <= i < len(self.toc.chapters)
        ]

        log_event(
            "two_pass.pass1_selected",
            request_id=request_id,
            question=question,
            selected_indices=valid_indices,
            selected_headings=[self.toc.chapters[i].heading for i in valid_indices],
            reasoning=selection.reasoning,
        )

        # Enforce budget: add chapters in order until we'd exceed the limit.
        chapters: list[TocChapter] = []
        total_bytes = 0
        for i in valid_indices:
            ch = self.toc.chapters[i]
            ch_text = self.toc.chapter_text(ch)
            ch_bytes = len(ch_text.encode("utf-8"))
            if total_bytes + ch_bytes > MAX_CONTEXT_BYTES and chapters:
                log_event(
                    "two_pass.pass1_budget_exceeded",
                    level="warning",
                    request_id=request_id,
                    question=question,
                    dropped_chapter=ch.heading,
                    total_bytes=total_bytes,
                    budget=MAX_CONTEXT_BYTES,
                )
                break
            chapters.append(ch)
            total_bytes += ch_bytes

        return chapters

    def _pass2_answer(
        self,
        question: str,
        history: list[dict[str, str]],
        chapters: list[TocChapter],
        request_id: str | None = None,
    ) -> TwoPassResult:
        chapter_texts: list[str] = []
        chapter_headings: list[str] = []
        for ch in chapters:
            path = " > ".join(ch.heading_path)
            text = self.toc.chapter_text(ch)
            chapter_texts.append(f"=== {path} ({ch.corpus}) ===\n{text}\n=== END ===")
            chapter_headings.append(path)

        context_block = "\n\n".join(chapter_texts)
        history_block = _format_history(history)

        system_prompt = (
            "You are a QA assistant for Somerville, MA municipal law.\n"
            "Answer the user's question using ONLY the provided law text.\n"
            "Every material claim must be supported by an exact quote from the text.\n"
            "Do NOT invent information not present in the provided text.\n"
            "Do NOT include any disclaimers, legal warnings, or 'informational only' notices.\n"
            "Use bold sparingly — only for the single most important fact, if any.\n\n"
            "Return JSON:\n"
            "{\"answer_markdown\": string, \"citations\": [{\"quote\": string, \"source_heading\": string, \"reason\": string}], "
            "\"confidence\": \"low|medium|high\", \"clarification_question\": string|null}"
        )

        user_prompt = (
            f"Conversation history:\n{history_block}\n\n"
            f"Question: {question}\n\n"
            f"Law text:\n{context_block}"
        )

        log_event(
            "two_pass.pass2_request",
            request_id=request_id,
            question=question,
            chapter_count=len(chapters),
            chapter_headings=chapter_headings,
            context_length=len(context_block),
            user_prompt_length=len(user_prompt),
        )

        raw = self.provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=2000,
        ).content

        log_event(
            "two_pass.pass2_response",
            request_id=request_id,
            question=question,
            raw_response=raw,
        )

        try:
            payload = _parse_answer_payload(raw)
        except Exception as exc:
            log_event(
                "two_pass.pass2_parse_failed",
                level="error",
                request_id=request_id,
                question=question,
                error=serialize_exception(exc),
            )
            return TwoPassResult(
                answer="I encountered an error processing the law text. Please try rephrasing your question.",
                citations=[],
                confidence="low",
                needs_clarification=True,
                clarification_question="Could you rephrase your question?",
                selected_chapters=[ch.heading for ch in chapters],
            )

        citations = [
            TwoPassCitation(
                quote=c.quote.strip(),
                source_heading=c.source_heading.strip(),
                reason=c.reason.strip(),
            )
            for c in payload.citations
        ]

        return TwoPassResult(
            answer=payload.answer_markdown.strip(),
            citations=citations,
            confidence=payload.confidence,
            needs_clarification=bool(payload.clarification_question),
            clarification_question=payload.clarification_question,
            selected_chapters=[ch.heading for ch in chapters],
        )


def _format_history(history: list[dict[str, str]]) -> str:
    if not history:
        return "(no prior messages)"
    lines = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "").strip()
        if content:
            lines.append(f"- {role}: {content}")
    return "\n".join(lines) if lines else "(no prior messages)"


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError("No JSON object found in model response.")
    return match.group(0)


def _parse_toc_selection(raw: str) -> TocSelection:
    candidate = _extract_json(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Pass 1: invalid JSON: {exc}") from exc
    try:
        return TocSelection.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"Pass 1: schema validation failed: {exc}") from exc


def _parse_answer_payload(raw: str) -> AnswerPayload:
    candidate = _extract_json(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Pass 2: invalid JSON: {exc}") from exc
    try:
        return AnswerPayload.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"Pass 2: schema validation failed: {exc}") from exc
