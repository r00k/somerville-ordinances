from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from .config import AppSettings
from .corpus import CorpusBundle
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

    def ask(self, question: str, history: list[dict[str, str]]) -> ChatResult:
        routed_corpora = route_query_corpora(question)
        hits, trace = self.index.search(
            question,
            allowed_corpora=routed_corpora,
            top_k=self.settings.retrieval_top_k,
            excerpt_chars=self.settings.retrieval_excerpt_chars,
            min_score=self.settings.retrieval_min_score,
        )

        if not hits:
            return self._refusal_result(
                trace=trace,
                clarification_question="Could you include more detail (topic, article, or section) so I can retrieve supporting law text?",
            )

        retrieval_level = retrieval_confidence(hits, trace)
        try:
            candidate = self._generate_validated_answer(question, history, hits)
        except Exception:
            return self._refusal_result(
                trace=trace,
                clarification_question=(
                    "The model response could not be validated. Please retry or narrow the question for a more targeted lookup."
                ),
            )
        used_long_context = False

        if self._should_run_long_context_verification(question, retrieval_level):
            expanded_hits, _expanded_trace = self.index.search(
                question,
                allowed_corpora=routed_corpora,
                top_k=self.settings.long_context_top_k,
                excerpt_chars=self.settings.retrieval_excerpt_chars,
                min_score=max(0.0, self.settings.retrieval_min_score * 0.5),
            )
            if expanded_hits:
                try:
                    alt_candidate = self._generate_validated_answer(question, history, expanded_hits)
                except Exception:
                    alt_candidate = None
                if alt_candidate is not None:
                    candidate = self._choose_candidate(candidate, alt_candidate)
                    used_long_context = True

        final_confidence = compute_final_confidence(
            question=question,
            model_confidence=candidate.confidence,
            retrieval_confidence_level=retrieval_level,
            citations=candidate.citations,
            hits=hits,
        )

        if candidate.insufficient_context or not candidate.citations:
            return self._refusal_result(
                trace=trace,
                clarification_question=(
                    candidate.clarification_question
                    or "I can only answer when I can cite exact ordinance text. Can you narrow the scope?"
                ),
                confidence=final_confidence,
                used_long_context=used_long_context,
            )

        citation_views = self._to_citation_views(candidate.citations, hits)
        answer = candidate.answer_markdown.strip()
        if DISCLAIMER.lower() not in answer.lower():
            answer = f"{answer}\n\n{DISCLAIMER}"

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

    def _generate_validated_answer(
        self,
        question: str,
        history: list[dict[str, str]],
        hits: list[RetrievedSection],
    ) -> GeneratedAnswer:
        payload = self._call_model_for_answer(question, history, hits)
        answer = payload_to_generated(payload)

        errors, valid_citations = validate_citations(answer.citations, hits)
        if errors and not answer.insufficient_context:
            repaired = self._call_model_for_repair(question, history, hits, payload, errors)
            repaired_answer = payload_to_generated(repaired)
            errors, valid_citations = validate_citations(repaired_answer.citations, hits)
            answer = repaired_answer

        if errors and not answer.insufficient_context:
            return GeneratedAnswer(
                answer_markdown="I cannot provide a grounded answer from the retrieved legal text.",
                citations=[],
                confidence="low",
                insufficient_context=True,
                clarification_question="Could you narrow the question or reference a specific ordinance topic?",
            )

        return GeneratedAnswer(
            answer_markdown=answer.answer_markdown,
            citations=valid_citations,
            confidence=answer.confidence,
            insufficient_context=answer.insufficient_context,
            clarification_question=answer.clarification_question,
        )

    def _call_model_for_answer(
        self,
        question: str,
        history: list[dict[str, str]],
        hits: list[RetrievedSection],
    ) -> ModelAnswerPayload:
        system_prompt = (
            "You are a legal QA assistant for Somerville municipal law. "
            "Correctness is the top priority. Use ONLY the retrieved ordinance sections. "
            "If evidence is incomplete, set insufficient_context=true and ask a clarification question. "
            "Do not invent section IDs, percentages, or permissions. "
            "Return strictly valid JSON matching the requested schema."
        )

        user_prompt = build_answer_user_prompt(question=question, history=history, hits=hits)
        raw = self.provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=1800,
        ).content
        return parse_model_payload(raw)

    def _call_model_for_repair(
        self,
        question: str,
        history: list[dict[str, str]],
        hits: list[RetrievedSection],
        prior_payload: ModelAnswerPayload,
        errors: list[str],
    ) -> ModelAnswerPayload:
        system_prompt = (
            "You are repairing citation grounding errors in a legal QA response. "
            "Fix citation corpus/secid/quote so every quote is an exact substring of provided sections, "
            "or set insufficient_context=true. Return JSON only."
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
            "{\"answer_markdown\": string, \"citations\": [{\"corpus\": \"non_zoning|zoning\", \"secid\": string, \"quote\": string, \"reason\": string}], \"confidence\": \"low|medium|high\", \"insufficient_context\": boolean, \"clarification_question\": string|null}\n\n"
            "Retrieved sections:\n"
            f"{render_section_context(hits)}"
        )

        raw = self.provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=1800,
        ).content
        return parse_model_payload(raw)

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
        if current.insufficient_context and not alternative.insufficient_context:
            return alternative
        if alternative.insufficient_context and not current.insufficient_context:
            return current

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
    ) -> ChatResult:
        answer = (
            "I can’t answer that reliably from the retrieved Somerville law text without risking an incorrect statement. "
            "Please narrow the question so I can cite exact sections.\n\n"
            f"{DISCLAIMER}"
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
        "{\"answer_markdown\": string, \"citations\": [{\"corpus\": \"non_zoning|zoning\", \"secid\": string, \"quote\": string, \"reason\": string}], \"confidence\": \"low|medium|high\", \"insufficient_context\": boolean, \"clarification_question\": string|null}\n\n"
        "Rules:\n"
        "1) Only answer using retrieved sections below.\n"
        "2) Every material claim must be supported by at least one citation.\n"
        "3) citation.quote must be an exact excerpt from that section text.\n"
        "4) If uncertain or incomplete, set insufficient_context=true.\n"
        "5) Keep answer concise and include numbers/conditions directly when present.\n\n"
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
        insufficient_context=payload.insufficient_context,
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
