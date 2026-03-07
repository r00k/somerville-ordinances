from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from .config import AppSettings
from .corpus import CorpusBundle
from .observability import log_event, serialize_exception
from .provider import CorpusModel
from .routing import route_question
from .types import CitationRecord, Confidence, CorpusName, GeneratedAnswer


class CitationPayload(BaseModel):
    corpus: Literal["non_zoning", "zoning"]
    secid: str
    quote: str
    reason: str = ""


class ModelAnswerPayload(BaseModel):
    answer_markdown: str
    citations: list[CitationPayload] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    insufficient_context: bool = False
    clarification_question: Optional[str] = None


@dataclass(frozen=True)
class CitationView:
    corpus: CorpusName
    secid: str
    heading: str
    source_file: str
    quote: str
    reason: str


@dataclass(frozen=True)
class ChatResult:
    answer: str
    citations: list[CitationView]
    confidence: Confidence
    refused: bool
    needs_clarification: bool
    clarification_question: str | None
    routed_corpus: CorpusName | None


class AnswerEngine:
    def __init__(
        self,
        *,
        settings: AppSettings,
        corpus_bundle: CorpusBundle,
        models: dict[CorpusName, CorpusModel],
    ):
        self.settings = settings
        self.bundle = corpus_bundle
        self.models = models

    def ask(
        self,
        question: str,
        history: list[dict[str, str]],
        request_id: str | None = None,
    ) -> ChatResult:
        log_event(
            "qa.assistance_attempt_started",
            request_id=request_id,
            question=question,
            history=history,
            model=self.settings.model_name,
        )

        routing = route_question(question)
        log_event(
            "qa.routing_decision",
            request_id=request_id,
            question=question,
            routed_corpus=routing.corpus,
            needs_clarification=routing.needs_clarification,
            reason=routing.reason,
        )

        model = self.models.get(routing.corpus)
        if model is None:
            fallback_corpus: CorpusName = "non_zoning" if routing.corpus == "zoning" else "zoning"
            model = self.models.get(fallback_corpus)
            routing = type(routing)(
                corpus=fallback_corpus,
                needs_clarification=False,
                clarification_question=None,
                reason="fallback_corpus",
            )
            if model is None:
                return ChatResult(
                    answer="No corpus models are available.",
                    citations=[],
                    confidence="low",
                    refused=False,
                    needs_clarification=False,
                    clarification_question=None,
                    routed_corpus=None,
                )

        log_event(
            "qa.model_request_sent",
            request_id=request_id,
            question=question,
            routed_corpus=routing.corpus,
            model=model.model_name,
        )

        try:
            raw = model.generate(question=question, history=history)
        except Exception as exc:
            log_event(
                "qa.model_generation_failed",
                level="error",
                request_id=request_id,
                question=question,
                routed_corpus=routing.corpus,
                error=serialize_exception(exc),
            )
            return ChatResult(
                answer="The model request failed, but here's what I can tell you: I don't have a response for this question right now. Please try again.",
                citations=[],
                confidence="low",
                refused=False,
                needs_clarification=False,
                clarification_question=None,
                routed_corpus=routing.corpus,
            )

        log_event(
            "qa.model_response_received",
            request_id=request_id,
            question=question,
            routed_corpus=routing.corpus,
            raw_response=raw,
        )

        try:
            payload = parse_model_payload(raw)
        except Exception as exc:
            log_event(
                "qa.model_response_parse_failed",
                level="error",
                request_id=request_id,
                question=question,
                routed_corpus=routing.corpus,
                error=serialize_exception(exc),
            )
            return ChatResult(
                answer=raw.strip() or "I wasn't able to produce a structured answer for this question.",
                citations=[],
                confidence="low",
                refused=False,
                needs_clarification=False,
                clarification_question=None,
                routed_corpus=routing.corpus,
            )

        candidate = payload_to_generated(payload)
        errors, valid_citations = validate_citations(
            citations=candidate.citations,
            bundle=self.bundle,
            expected_corpus=routing.corpus,
        )

        log_event(
            "qa.citation_validation_completed",
            request_id=request_id,
            question=question,
            routed_corpus=routing.corpus,
            validation_error_count=len(errors),
            validation_errors=errors,
            valid_citation_count=len(valid_citations),
        )

        answer = candidate.answer_markdown.strip()
        if not answer:
            answer = "I wasn't able to produce a clear answer for this question."

        citations = self._to_citation_views(valid_citations)
        response_confidence: Confidence = "low" if (candidate.insufficient_context or errors or not valid_citations) else candidate.confidence

        if candidate.insufficient_context:
            log_event(
                "qa.partial_answer_emitted",
                level="warning",
                request_id=request_id,
                question=question,
                routed_corpus=routing.corpus,
                reason="model_marked_insufficient_context",
            )

        log_event(
            "qa.assistance_attempt_completed",
            request_id=request_id,
            question=question,
            routed_corpus=routing.corpus,
            confidence=response_confidence,
            answer=answer,
            citations=[citation.__dict__ for citation in citations],
        )

        return ChatResult(
            answer=answer,
            citations=citations,
            confidence=response_confidence,
            refused=False,
            needs_clarification=bool(candidate.clarification_question) or candidate.insufficient_context,
            clarification_question=candidate.clarification_question,
            routed_corpus=routing.corpus,
        )

    def _to_citation_views(self, citations: list[CitationRecord]) -> list[CitationView]:
        views: list[CitationView] = []
        for citation in citations:
            section = self.bundle.by_key.get((citation.corpus, citation.secid))
            if section is None:
                continue
            views.append(
                CitationView(
                    corpus=citation.corpus,
                    secid=citation.secid,
                    heading=section.heading,
                    source_file=section.source_file,
                    quote=citation.quote,
                    reason=citation.reason,
                )
            )
        return views




def parse_model_payload(raw_content: str) -> ModelAnswerPayload:
    raw_content = raw_content.strip()
    if not raw_content:
        raise RuntimeError("Model returned an empty response.")

    candidate = extract_json_object(raw_content)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model did not return valid JSON: {exc}") from exc

    try:
        return ModelAnswerPayload.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError(f"Model JSON failed schema validation: {exc}") from exc


def extract_json_object(text: str) -> str:
    if text.startswith("{") and text.endswith("}"):
        return text

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError("No JSON object found in model response.")
    return match.group(0)


def payload_to_generated(payload: ModelAnswerPayload) -> GeneratedAnswer:
    citations = [
        CitationRecord(
            corpus=item.corpus,
            secid=item.secid,
            quote=item.quote.strip(),
            reason=item.reason.strip(),
        )
        for item in payload.citations
    ]
    return GeneratedAnswer(
        answer_markdown=payload.answer_markdown.strip(),
        citations=citations,
        confidence=payload.confidence,
        insufficient_context=payload.insufficient_context,
        clarification_question=payload.clarification_question,
    )


def validate_citations(
    *,
    citations: list[CitationRecord],
    bundle: CorpusBundle,
    expected_corpus: CorpusName,
) -> tuple[list[str], list[CitationRecord]]:
    errors: list[str] = []
    valid: list[CitationRecord] = []
    seen: set[tuple[str, str]] = set()

    for citation in citations:
        if citation.corpus != expected_corpus:
            errors.append(
                f"citation corpus mismatch: expected {expected_corpus}, got {citation.corpus}:{citation.secid}"
            )
            continue

        section = bundle.by_key.get((citation.corpus, citation.secid))
        if section is None:
            errors.append(f"citation references unknown section {citation.corpus}:{citation.secid}")
            continue

        quote = citation.quote.strip()
        if len(quote) < 18:
            errors.append(f"citation quote too short for {citation.corpus}:{citation.secid}")
            continue

        if normalize_for_match(quote) not in normalize_for_match(section.text):
            errors.append(f"citation quote not found in section text for {citation.corpus}:{citation.secid}")
            continue

        dedupe_key = (citation.secid, normalize_for_match(quote))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        valid.append(citation)

    return errors, valid


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()
