from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from .config import AppSettings
from .corpus import CorpusBundle
from .observability import log_event, serialize_exception
from .provider import ModelProvider
from .retrieval import (
    RetrievalTrace,
    SectionIndex,
    is_broad_query,
    retrieval_confidence,
    route_query_corpora,
)
from .types import CitationRecord, Confidence, CorpusName, GeneratedAnswer, RetrievedSection


DISCLAIMER = "_Informational only, not legal advice. Verify with official Somerville publications or the City before acting._"


class CitationPayload(BaseModel):
    corpus: Literal["non_zoning", "zoning"]
    secid: str
    quote: str
    reason: str = ""


class ModelAnswerPayload(BaseModel):
    answer_markdown: str
    citations: list[CitationPayload] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    clarification_question: Optional[str] = None


@dataclass(frozen=True)
class CitationView:
    corpus: CorpusName
    secid: str
    heading: str
    source_file: str
    quote: str
    reason: str
    score: float


@dataclass(frozen=True)
class ChatResult:
    answer: str
    citations: list[CitationView]
    confidence: Confidence
    refused: bool
    needs_clarification: bool
    clarification_question: str | None
    used_long_context_verification: bool
    retrieval_trace: RetrievalTrace


class AnswerEngine:
    def __init__(
        self,
        *,
        settings: AppSettings,
        corpus_bundle: CorpusBundle,
        index: SectionIndex,
        provider: ModelProvider,
    ):
        self.settings = settings
        self.bundle = corpus_bundle
        self.index = index
        self.provider = provider

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
            provider=self.provider.name,
            model=self.settings.model_name,
        )

        search_query = self._build_search_query(question, history)
        routed_corpora = route_query_corpora(search_query)
        hits, trace = self.index.search(
            search_query,
            allowed_corpora=routed_corpora,
            top_k=self.settings.retrieval_top_k,
            excerpt_chars=self.settings.retrieval_excerpt_chars,
            min_score=self.settings.retrieval_min_score,
        )

        log_event(
            "qa.retrieval_completed",
            request_id=request_id,
            question=question,
            requested_corpora=sorted(routed_corpora),
            retrieval_trace={
                "query_tokens": trace.query_tokens,
                "top_score": trace.top_score,
                "second_score": trace.second_score,
            },
            retrieved_sections=[
                {
                    "corpus": hit.section.corpus,
                    "secid": hit.section.secid,
                    "heading": hit.section.heading,
                    "score": hit.score,
                }
                for hit in hits
            ],
        )

        if not hits:
            return self._refusal_result(
                trace=trace,
                clarification_question="Could you include more detail (topic, article, or section) so I can retrieve supporting law text?",
                request_id=request_id,
                question=question,
                refusal_reason="no_retrieval_hits",
            )

        retrieval_level = retrieval_confidence(hits, trace)
        try:
            candidate = self._generate_validated_answer(
                question,
                history,
                hits,
                request_id=request_id,
            )
        except Exception as exc:
            return self._refusal_result(
                trace=trace,
                clarification_question=(
                    "The model response could not be validated. Please retry or narrow the question for a more targeted lookup."
                ),
                request_id=request_id,
                question=question,
                refusal_reason="model_generation_failed",
                refusal_error=serialize_exception(exc),
            )
        used_long_context = False

        if self._should_run_long_context_verification(question, retrieval_level):
            log_event(
                "qa.long_context_verification_started",
                request_id=request_id,
                question=question,
                retrieval_confidence=retrieval_level,
                long_context_top_k=self.settings.long_context_top_k,
            )
            expanded_hits, _expanded_trace = self.index.search(
                question,
                allowed_corpora=routed_corpora,
                top_k=self.settings.long_context_top_k,
                excerpt_chars=self.settings.retrieval_excerpt_chars,
                min_score=max(0.0, self.settings.retrieval_min_score * 0.5),
            )
            if expanded_hits:
                try:
                    alt_candidate = self._generate_validated_answer(
                        question,
                        history,
                        expanded_hits,
                        request_id=request_id,
                    )
                except Exception as exc:
                    log_event(
                        "qa.long_context_verification_failed",
                        level="warning",
                        request_id=request_id,
                        question=question,
                        error=serialize_exception(exc),
                    )
                    alt_candidate = None
                if alt_candidate is not None:
                    candidate = self._choose_candidate(candidate, alt_candidate)
                    used_long_context = True
                    log_event(
                        "qa.long_context_verification_completed",
                        request_id=request_id,
                        question=question,
                        selected_confidence=candidate.confidence,
                        selected_citation_count=len(candidate.citations),
                    )

        final_confidence = compute_final_confidence(
            question=question,
            model_confidence=candidate.confidence,
            retrieval_confidence_level=retrieval_level,
            citations=candidate.citations,
            hits=hits,
        )

        citation_views = self._to_citation_views(candidate.citations, hits)
        answer = candidate.answer_markdown.strip()

        log_event(
            "qa.assistance_attempt_completed",
            request_id=request_id,
            question=question,
            confidence=final_confidence,
            used_long_context_verification=used_long_context,
            answer=answer,
            citations=[
                {
                    "corpus": citation.corpus,
                    "secid": citation.secid,
                    "quote": citation.quote,
                    "reason": citation.reason,
                }
                for citation in candidate.citations
            ],
        )

        return ChatResult(
            answer=answer,
            citations=citation_views,
            confidence=final_confidence,
            refused=False,
            needs_clarification=bool(candidate.clarification_question),
            clarification_question=candidate.clarification_question,
            used_long_context_verification=used_long_context,
            retrieval_trace=trace,
        )

    @staticmethod
    def _build_search_query(question: str, history: list[dict[str, str]]) -> str:
        """Combine the current question with conversation history for retrieval."""
        if len(history) < 2:
            return question
        context_parts = [msg["content"] for msg in history[:-1]]
        context_parts.append(question)
        return " ".join(context_parts)

    def _generate_validated_answer(
        self,
        question: str,
        history: list[dict[str, str]],
        hits: list[RetrievedSection],
        request_id: str | None = None,
    ) -> GeneratedAnswer:
        payload = self._call_model_for_answer(question, history, hits, request_id=request_id)
        answer = payload_to_generated(payload)

        errors, valid_citations = validate_citations(answer.citations, hits)
        log_event(
            "qa.citation_validation_completed",
            request_id=request_id,
            question=question,
            stage="initial",
            validation_error_count=len(errors),
            validation_errors=errors,
            valid_citation_count=len(valid_citations),
        )
        if errors:
            log_event(
                "qa.citation_repair_requested",
                level="warning",
                request_id=request_id,
                question=question,
                validation_errors=errors,
            )

            repaired = self._call_model_for_repair(
                question,
                history,
                hits,
                payload,
                errors,
                request_id=request_id,
            )
            repaired_answer = payload_to_generated(repaired)
            errors, valid_citations = validate_citations(repaired_answer.citations, hits)
            answer = repaired_answer

            log_event(
                "qa.citation_validation_completed",
                request_id=request_id,
                question=question,
                stage="repair",
                validation_error_count=len(errors),
                validation_errors=errors,
                valid_citation_count=len(valid_citations),
            )

        if errors:
            log_event(
                "qa.citation_validation_failed",
                level="warning",
                request_id=request_id,
                question=question,
                validation_errors=errors,
            )
            return GeneratedAnswer(
                answer_markdown="I cannot provide a grounded answer from the retrieved legal text.",
                citations=[],
                confidence="low",
                clarification_question="Could you narrow the question or reference a specific ordinance topic?",
            )

        return GeneratedAnswer(
            answer_markdown=answer.answer_markdown,
            citations=valid_citations,
            confidence=answer.confidence,
            clarification_question=answer.clarification_question,
        )

    def _call_model_for_answer(
        self,
        question: str,
        history: list[dict[str, str]],
        hits: list[RetrievedSection],
        request_id: str | None = None,
    ) -> ModelAnswerPayload:
        system_prompt = (
            "You are a QA assistant for Somerville municipal law. "
            "Use ONLY the retrieved ordinance sections. "
            "Answer as fully as you can from the available sections, even if they don't cover every aspect of the topic. "
            "ALWAYS provide an answer using whatever relevant sections are available. "
            "Do not invent section IDs, percentages, or permissions. "
            "Each citation quote MUST be copied exactly from the section text (exact substring match). "
            "Do NOT include any disclaimers, legal warnings, or 'informational only' notices in your answer. "
            "Use bold sparingly — only for the single most important fact in your answer, if any. Prefer plain text. "
            "Return strictly valid JSON matching the requested schema."
        )

        user_prompt = build_answer_user_prompt(question=question, history=history, hits=hits)
        log_event(
            "qa.model_request_sent",
            request_id=request_id,
            question=question,
            stage="answer",
            provider=self.provider.name,
            model=self.settings.model_name,
            system_prompt_length=len(system_prompt),
            user_prompt_length=len(user_prompt),
            max_tokens=1800,
        )
        raw = self.provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=1800,
        ).content
        log_event(
            "qa.model_response_received",
            request_id=request_id,
            question=question,
            stage="answer",
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
                stage="answer",
                raw_response=raw,
                error=serialize_exception(exc),
            )
            raise

        log_event(
            "qa.model_response_parsed",
            request_id=request_id,
            question=question,
            stage="answer",
            payload=payload,
        )
        return payload

    def _call_model_for_repair(
        self,
        question: str,
        history: list[dict[str, str]],
        hits: list[RetrievedSection],
        prior_payload: ModelAnswerPayload,
        errors: list[str],
        request_id: str | None = None,
    ) -> ModelAnswerPayload:
        system_prompt = (
            "You are repairing citation grounding errors in a legal QA response. "
            "Fix citation corpus/secid/quote so every quote is an exact substring of provided sections. "
            "Return JSON only."
        )

        prior_json = prior_payload.model_dump_json()
        issues = "\n".join(f"- {error}" for error in errors)
        user_prompt = (
            f"Question: {question}\n\n"
            "Previous answer JSON:\n"
            f"{prior_json}\n\n"
            "Validation errors:\n"
            f"{issues}\n\n"
            "Use the same schema:\n"
            "{\"answer_markdown\": string, \"citations\": [{\"corpus\": \"non_zoning|zoning\", \"secid\": string, \"quote\": string, \"reason\": string}], \"confidence\": \"low|medium|high\", \"clarification_question\": string|null}\n\n"
            "Retrieved sections:\n"
            f"{render_section_context(hits)}"
        )

        log_event(
            "qa.model_request_sent",
            request_id=request_id,
            question=question,
            stage="repair",
            provider=self.provider.name,
            model=self.settings.model_name,
            validation_errors=errors,
            system_prompt_length=len(system_prompt),
            user_prompt_length=len(user_prompt),
            max_tokens=1800,
        )
        raw = self.provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=1800,
        ).content
        log_event(
            "qa.model_response_received",
            request_id=request_id,
            question=question,
            stage="repair",
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
                stage="repair",
                raw_response=raw,
                error=serialize_exception(exc),
            )
            raise

        log_event(
            "qa.model_response_parsed",
            request_id=request_id,
            question=question,
            stage="repair",
            payload=payload,
        )
        return payload

    def _to_citation_views(
        self,
        citations: list[CitationRecord],
        hits: list[RetrievedSection],
    ) -> list[CitationView]:
        hit_index = {(hit.section.corpus, hit.section.secid): hit for hit in hits}
        views: list[CitationView] = []
        for citation in citations:
            key = (citation.corpus, citation.secid)
            section = self.bundle.by_key.get(key)
            if not section:
                continue
            hit = hit_index.get(key)
            views.append(
                CitationView(
                    corpus=citation.corpus,
                    secid=citation.secid,
                    heading=section.heading,
                    source_file=section.source_file,
                    quote=citation.quote,
                    reason=citation.reason,
                    score=hit.score if hit else 0.0,
                )
            )
        return views

    def _should_run_long_context_verification(self, question: str, retrieval_level: Confidence) -> bool:
        if not self.settings.enable_long_context_verification:
            return False

        threshold = self.settings.long_context_trigger_min_confidence
        threshold_rank = confidence_rank(threshold if threshold in {"low", "medium", "high"} else "medium")
        retrieval_rank = confidence_rank(retrieval_level)

        return is_broad_query(question) or retrieval_rank <= threshold_rank

    @staticmethod
    def _choose_candidate(current: GeneratedAnswer, alternative: GeneratedAnswer) -> GeneratedAnswer:
        current_rank = confidence_rank(current.confidence)
        alt_rank = confidence_rank(alternative.confidence)
        if alt_rank > current_rank:
            return alternative
        if current_rank > alt_rank:
            return current

        if len(alternative.citations) > len(current.citations):
            return alternative
        return current

    def _refusal_result(
        self,
        *,
        trace: RetrievalTrace,
        clarification_question: str,
        confidence: Confidence = "low",
        used_long_context: bool = False,
        request_id: str | None = None,
        question: str | None = None,
        refusal_reason: str = "insufficient_grounding",
        refusal_error: dict[str, str] | None = None,
    ) -> ChatResult:
        answer = (
            "I can’t answer that reliably from the retrieved Somerville law text without risking an incorrect statement. "
            "Please narrow the question so I can cite exact sections."
        )
        log_event(
            "qa.assistance_attempt_refused",
            level="error" if refusal_error else "warning",
            request_id=request_id,
            question=question,
            reason=refusal_reason,
            error=refusal_error,
            confidence=confidence,
            clarification_question=clarification_question,
            used_long_context_verification=used_long_context,
            retrieval_trace={
                "requested_corpora": sorted(trace.requested_corpora),
                "query_tokens": trace.query_tokens,
                "top_score": trace.top_score,
                "second_score": trace.second_score,
            },
        )
        return ChatResult(
            answer=answer,
            citations=[],
            confidence=confidence,
            refused=True,
            needs_clarification=True,
            clarification_question=clarification_question,
            used_long_context_verification=used_long_context,
            retrieval_trace=trace,
        )


def build_answer_user_prompt(
    *,
    question: str,
    history: list[dict[str, str]],
    hits: list[RetrievedSection],
) -> str:
    history_lines: list[str] = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "").strip()
        if not content:
            continue
        history_lines.append(f"- {role}: {content}")
    history_block = "\n".join(history_lines) if history_lines else "(no prior messages)"

    return (
        "Return JSON with this schema exactly:\n"
        "{\"answer_markdown\": string, \"citations\": [{\"corpus\": \"non_zoning|zoning\", \"secid\": string, \"quote\": string, \"reason\": string}], \"confidence\": \"low|medium|high\", \"clarification_question\": string|null}\n\n"
        "Rules:\n"
        "1) Only answer using retrieved sections below.\n"
        "2) Every material claim must be supported by at least one citation.\n"
        "3) citation.quote must be an exact excerpt from that section text.\n"
        "4) Keep answer concise and include numbers/conditions directly when present.\n"
        "\n"
        "Conversation history:\n"
        f"{history_block}\n\n"
        "Question:\n"
        f"{question}\n\n"
        "Retrieved sections:\n"
        f"{render_section_context(hits)}"
    )


def render_section_context(hits: list[RetrievedSection]) -> str:
    lines: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        section = hit.section
        heading_path = " > ".join(section.heading_path) if section.heading_path else section.heading
        lines.append(f"[SECTION S{idx}]")
        lines.append(f"corpus: {section.corpus}")
        lines.append(f"secid: {section.secid}")
        lines.append(f"heading: {heading_path}")
        lines.append("text:")
        lines.append(hit.excerpt)
        lines.append("[END SECTION]")
        lines.append("")
    return "\n".join(lines).strip()


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
        clarification_question=payload.clarification_question,
    )


def validate_citations(
    citations: list[CitationRecord],
    hits: list[RetrievedSection],
) -> tuple[list[str], list[CitationRecord]]:
    errors: list[str] = []
    valid: list[CitationRecord] = []

    section_lookup = {(hit.section.corpus, hit.section.secid): hit.section for hit in hits}
    seen: set[tuple[str, str, str]] = set()

    for citation in citations:
        key = (citation.corpus, citation.secid)
        section = section_lookup.get(key)
        if section is None:
            errors.append(f"citation references non-retrieved section {citation.corpus}:{citation.secid}")
            continue

        quote = citation.quote.strip()
        if len(quote) < 18:
            errors.append(f"citation quote is too short for {citation.corpus}:{citation.secid}")
            continue

        if normalize_for_match(quote) not in normalize_for_match(section.text):
            errors.append(f"citation quote not found in section text for {citation.corpus}:{citation.secid}")
            continue

        dedupe_key = (citation.corpus, citation.secid, normalize_for_match(quote))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        valid.append(citation)

    return errors, valid



def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def confidence_rank(confidence: str) -> int:
    order = {"low": 0, "medium": 1, "high": 2}
    return order.get(confidence, 0)


def lower_confidence(a: Confidence, b: Confidence) -> Confidence:
    return a if confidence_rank(a) <= confidence_rank(b) else b


def compute_final_confidence(
    *,
    question: str,
    model_confidence: Confidence,
    retrieval_confidence_level: Confidence,
    citations: list[CitationRecord],
    hits: list[RetrievedSection],
) -> Confidence:
    base = lower_confidence(model_confidence, retrieval_confidence_level)
    if base == "low":
        return "low"

    # Promote direct single-fact questions when evidence is strong and validated.
    if not is_direct_fact_question(question):
        return base
    if not citations:
        return base

    hit_scores = {(hit.section.corpus, hit.section.secid): hit.score for hit in hits}
    cited_scores = [
        hit_scores.get((citation.corpus, citation.secid), 0.0)
        for citation in citations
    ]
    max_score = max(cited_scores) if cited_scores else 0.0
    longest_quote = max((len(citation.quote.strip()) for citation in citations), default=0)

    if max_score >= 20.0 and longest_quote >= 35:
        return "high"

    return base


def is_direct_fact_question(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    if not normalized:
        return False

    starts_with_fact = normalized.startswith("how many") or normalized.startswith("what is")
    if not starts_with_fact:
        return False

    # Multi-part prompts should stay more conservative.
    if " and " in normalized:
        return False

    disqualifiers = {
        "without permission",
        "unless",
        "exception",
        "requirements",
        "procedure",
        "process",
        "steps",
    }
    if any(token in normalized for token in disqualifiers):
        return False

    return True
