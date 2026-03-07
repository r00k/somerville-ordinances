from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .types import Confidence, CorpusName, CorpusSection, RetrievedSection


TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", flags=re.IGNORECASE)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "many",
    "people",
    "please",
    "tell",
    "somerville",
}

ZONING_HINTS = {
    "zoning",
    "district",
    "setback",
    "far",
    "floor area ratio",
    "inclusionary",
    "affordable",
    "site plan",
    "special permit",
    "demolition",
    "overlay",
    "building type",
    "lot",
    "szo",
    "article 12",
}
NON_ZONING_HINTS = {
    "charter",
    "city council",
    "mayor",
    "ordinance",
    "code of ordinances",
    "appendix",
    "board of health",
    "traffic commission",
    "petition",
    "city clerk",
}


@dataclass(frozen=True)
class RetrievalTrace:
    requested_corpora: set[CorpusName]
    query_tokens: list[str]
    top_score: float
    second_score: float


class SectionIndex:
    def __init__(self, sections: list[CorpusSection]):
        self.sections = sections
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._doc_lengths: list[int] = []
        self._avg_doc_len = 0.0

        for doc_id, section in enumerate(sections):
            terms = self.tokenize(section.text)
            term_counts = Counter(terms)
            doc_len = len(terms)
            self._doc_lengths.append(doc_len)
            for term, tf in term_counts.items():
                self._postings[term].append((doc_id, tf))

        if self._doc_lengths:
            self._avg_doc_len = sum(self._doc_lengths) / len(self._doc_lengths)

    @staticmethod
    def tokenize(text: str) -> list[str]:
        tokens = [normalize_token(match.group(0)) for match in TOKEN_RE.finditer(text)]
        return [token for token in tokens if token and token not in STOP_WORDS]

    def _idf(self, term: str) -> float:
        n_docs = len(self.sections)
        df = len(self._postings.get(term, []))
        if df == 0:
            return 0.0
        return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

    def search(
        self,
        query: str,
        *,
        allowed_corpora: set[CorpusName],
        top_k: int,
        excerpt_chars: int,
        min_score: float,
    ) -> tuple[list[RetrievedSection], RetrievalTrace]:
        query_lower = query.lower()
        query_tokens = expand_query_tokens(query_lower, self.tokenize(query))
        query_phrases = build_query_phrases(query_lower)
        scores: dict[int, float] = defaultdict(float)
        k1 = 1.2
        b = 0.75

        allowed_doc_ids = {
            idx for idx, section in enumerate(self.sections) if section.corpus in allowed_corpora
        }

        for term in query_tokens:
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            for doc_id, tf in postings:
                if doc_id not in allowed_doc_ids:
                    continue
                doc_len = max(1, self._doc_lengths[doc_id])
                denom = tf + k1 * (1 - b + b * doc_len / max(1.0, self._avg_doc_len))
                scores[doc_id] += idf * (tf * (k1 + 1) / denom)

        # Boost sections whose headings and text strongly match query phrasing.
        if query_tokens or query_phrases:
            for doc_id in allowed_doc_ids:
                section = self.sections[doc_id]
                heading = section.heading.lower()
                text = section.text.lower()

                bonus = 0.0
                for token in query_tokens:
                    if token in heading:
                        bonus += 0.9

                for phrase in query_phrases:
                    if phrase in heading:
                        bonus += 3.2
                    elif phrase in text:
                        bonus += 2.1

                if "city council" in query_lower and ("how many" in query_lower or "number" in query_lower):
                    if "city council consisting of" in text:
                        bonus += 4.2
                    if "composition" in heading:
                        bonus += 1.2

                if "affordable" in query_lower and "unit" in query_lower:
                    if "inclusionary" in text:
                        bonus += 1.5
                    if "%" in text:
                        bonus += 0.8

                if "demolish" in query_lower or "demolition" in query_lower:
                    if "demolition permit" in text or "demolition review" in text:
                        bonus += 2.4

                if bonus > 0:
                    scores[doc_id] += bonus

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)

        # Fallback when terms are too broad or filtered out.
        if not ranked and query_tokens:
            for doc_id in allowed_doc_ids:
                section = self.sections[doc_id]
                haystack = f"{section.heading} {section.text}".lower()
                overlap = sum(1 for token in query_tokens if token in haystack)
                if overlap:
                    scores[doc_id] = float(overlap)
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)

        results: list[RetrievedSection] = []
        for doc_id, score in ranked:
            if score < min_score:
                continue
            section = self.sections[doc_id]
            excerpt = build_excerpt(section.text, query_tokens, max_chars=excerpt_chars)
            results.append(RetrievedSection(section=section, score=score, excerpt=excerpt))
            if len(results) >= top_k:
                break

        top_score = results[0].score if results else 0.0
        second_score = results[1].score if len(results) > 1 else 0.0
        return (
            results,
            RetrievalTrace(
                requested_corpora=allowed_corpora,
                query_tokens=query_tokens,
                top_score=top_score,
                second_score=second_score,
            ),
        )


def build_excerpt(text: str, query_tokens: list[str], max_chars: int) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        clean = re.sub(r"\s+", " ", text).strip()
        return clean[:max_chars]

    scored: list[tuple[int, int, str]] = []
    lowered_tokens = set(query_tokens)
    for idx, paragraph in enumerate(paragraphs):
        para_terms = set(SectionIndex.tokenize(paragraph))
        overlap = len(para_terms & lowered_tokens)
        scored.append((overlap, -idx, paragraph))

    scored.sort(reverse=True)

    parts: list[str] = []
    used = 0
    for overlap, _, paragraph in scored:
        if not parts and overlap == 0:
            # include at least one paragraph even with zero lexical overlap
            pass
        compact = re.sub(r"\s+", " ", paragraph).strip()
        if not compact:
            continue
        if used + len(compact) > max_chars and parts:
            break
        if used + len(compact) > max_chars:
            compact = compact[: max(0, max_chars - used)].rstrip()
        if compact:
            parts.append(compact)
            used += len(compact) + 2
        if used >= max_chars:
            break
        if overlap == 0 and len(parts) >= 2:
            break

    excerpt = "\n\n".join(parts) if parts else re.sub(r"\s+", " ", paragraphs[0])
    return excerpt[:max_chars]


def route_query_corpora(query: str) -> set[CorpusName]:
    text = query.lower()
    zoning_score = sum(1 for hint in ZONING_HINTS if hint in text)
    non_zoning_score = sum(1 for hint in NON_ZONING_HINTS if hint in text)

    if zoning_score > 0 and non_zoning_score == 0:
        return {"zoning"}
    if non_zoning_score > 0 and zoning_score == 0:
        return {"non_zoning"}
    return {"non_zoning", "zoning"}


def retrieval_confidence(results: list[RetrievedSection], trace: RetrievalTrace) -> Confidence:
    if not results:
        return "low"

    ratio = trace.top_score / max(0.001, trace.second_score)
    if trace.top_score >= 5 and ratio >= 1.1:
        return "high"
    if trace.top_score >= 1.5:
        return "medium"
    return "low"


def is_broad_query(query: str) -> bool:
    lowered = query.lower()
    broad_markers = {
        "everything",
        "all rules",
        "all regulations",
        "give me all",
        "entire",
        "comprehensive",
        "summarize all",
    }
    if any(marker in lowered for marker in broad_markers):
        return True
    return len(query.split()) >= 28


def build_query_phrases(query: str) -> set[str]:
    words = [normalize_token(match.group(0)) for match in TOKEN_RE.finditer(query.lower())]
    words = [word for word in words if word and len(word) >= 2]
    phrases: set[str] = set()

    for idx in range(len(words) - 1):
        left = words[idx]
        right = words[idx + 1]
        if left in STOP_WORDS or right in STOP_WORDS:
            continue
        if len(left) < 3 or len(right) < 3:
            continue
        phrases.add(f"{left} {right}")

    if "city council" in query:
        phrases.add("city council")
    if "inclusionary zoning" in query:
        phrases.add("inclusionary zoning")
    if "demolish" in query:
        phrases.add("demolish")
    if "demolition" in query:
        phrases.add("demolition")

    return phrases


def normalize_token(token: str) -> str:
    token = token.lower().strip("'")
    if token.endswith("'s"):
        token = token[:-2]
    if token.endswith("s'"):
        token = token[:-1]
    return token


def expand_query_tokens(query: str, base_tokens: list[str]) -> list[str]:
    expanded = list(base_tokens)

    if "city council" in query and ("how many" in query or "number" in query):
        expanded.extend(["members", "composition", "consisting"])

    if "affordable" in query and "unit" in query:
        expanded.extend(["inclusionary", "percent", "dwelling"])

    if "demolish" in query or "demolition" in query:
        expanded.extend(["demolition", "permit", "review", "historic"])

    deduped: list[str] = []
    seen: set[str] = set()
    for token in expanded:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped
