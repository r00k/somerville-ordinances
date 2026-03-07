from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CorpusName = Literal["non_zoning", "zoning"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class CorpusSection:
    corpus: CorpusName
    secid: str
    tocid: str | None
    heading: str
    heading_path: tuple[str, ...]
    source_file: str
    ordinal: int
    text: str

    @property
    def key(self) -> tuple[CorpusName, str]:
        return self.corpus, self.secid

@dataclass(frozen=True)
class CitationRecord:
    corpus: CorpusName
    secid: str
    quote: str
    reason: str


@dataclass(frozen=True)
class GeneratedAnswer:
    answer_markdown: str
    citations: list[CitationRecord]
    confidence: Confidence
    insufficient_context: bool
    clarification_question: str | None
