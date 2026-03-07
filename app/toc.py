"""Parse markdown corpus files into a table of contents with chapter-level text extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import Stemmer
from rank_bm25 import BM25Okapi

from .types import CorpusName

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "of", "in", "to",
    "for", "with", "on", "at", "from", "by", "about", "as", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "and", "but", "or", "nor", "not", "no", "so", "if", "then", "than",
    "that", "this", "these", "those", "it", "its", "he", "she", "his",
    "her", "how", "what", "which", "who", "whom", "when", "where", "why",
    "long", "many", "much", "term", "terms",
    "keep", "get", "let", "put", "make", "go", "come", "take", "give",
    "need", "want", "know", "think", "say", "tell", "ask", "use",
    "my", "me", "your", "our", "am",
    # Domain-specific: too common in municipal law headings to be useful
    "ordinance", "city", "street", "section", "article",
    "somerville", "municipal", "law", "regulation", "regulations",
    "sec", "div", "division", "chapter", "appendix", "part",
})

# Synonym map: stemmed query term -> additional stemmed tokens to inject.
# This bridges vocabulary gaps where users search with everyday words
# but headings use formal/legal language. Keys and values are pre-stemmed.
_SYNONYMS: dict[str, list[str]] = {
    "tattoo": ["bodi", "art"],
    "pierc": ["bodi", "art"],
    "liquor": ["alcohol", "beverag"],
    "booz": ["alcohol", "beverag"],
    "chicken": ["non", "domest", "anim"],
    "rooster": ["non", "domest", "anim"],
    "goat": ["non", "domest", "anim"],
    "busk": ["street", "perform"],
    "crosswalk": ["pedestrian", "cross"],
    "jaywalk": ["pedestrian", "cross"],
    "airbnb": ["short", "rental"],
    "vrbo": ["short", "rental"],
    "landmark": ["histor", "district"],
    "pothol": ["highway", "road"],
    "rat": ["pest", "health"],
    "rodent": ["pest", "health"],
    "graffiti": ["offens", "properti"],
    "recycl": ["trash"],
    "garbag": ["trash"],
    "rubbish": ["trash"],
    "evict": ["hous", "stabl"],
    "landlord": ["hous", "rental"],
    "tenant": ["hous", "rental"],
    "uber": ["taxi", "vehicl"],
    "lyft": ["taxi", "vehicl"],
    "natur": ["fossil", "fuel"],  # "natural gas" -> fossil fuel free construction
}

_stemmer = Stemmer.Stemmer("english")
_TOKEN_RE = re.compile(r"\W+")


def _split(text: str) -> list[str]:
    """Split text into lowercase tokens, dropping single-character tokens."""
    return [t for t in _TOKEN_RE.split(text.lower()) if len(t) > 1]


def _tokenize(text: str) -> list[str]:
    """Split, remove stop words, and stem."""
    return _stemmer.stemWords([t for t in _split(text) if t not in _STOP_WORDS])


def _tokenize_full(text: str) -> list[str]:
    """Split and stem without stop-word filtering (for all-stop-word fallback)."""
    return _stemmer.stemWords(_split(text))


def _expand_synonyms(stemmed: list[str]) -> list[str]:
    """Append synonym tokens for any stemmed query term that has a synonym entry."""
    extra: list[str] = []
    for token in stemmed:
        if token in _SYNONYMS:
            extra.extend(_SYNONYMS[token])
    if not extra:
        return stemmed
    return stemmed + extra


@dataclass(frozen=True)
class TocSearchHit:
    """A search result from the table of contents."""

    chapter_index: int
    corpus: CorpusName
    heading: str
    heading_path: tuple[str, ...]
    subheadings: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class TocChapter:
    """A chapter/article-level entry in the table of contents."""

    corpus: CorpusName
    heading: str
    heading_path: tuple[str, ...]
    level: int
    start_line: int  # 0-indexed inclusive
    end_line: int  # 0-indexed exclusive
    subheadings: tuple[str, ...]  # section-level headings within this chapter


@dataclass(frozen=True)
class CorpusToc:
    """Full table of contents for both corpora, with access to raw lines for text extraction."""

    chapters: list[TocChapter]
    _lines_by_corpus: dict[CorpusName, list[str]]
    # BM25 indices (built at construction time via build_corpus_toc)
    _heading_token_sets: list[set[str]] = field(repr=False)
    _bm25_path: BM25Okapi = field(repr=False)
    _bm25_sub: BM25Okapi = field(repr=False)
    # Fallback indices for all-stop-word queries (include stop words)
    _heading_token_sets_full: list[set[str]] = field(repr=False)
    _bm25_path_full: BM25Okapi = field(repr=False)
    _bm25_sub_full: BM25Okapi = field(repr=False)

    def chapter_text(self, chapter: TocChapter) -> str:
        lines = self._lines_by_corpus[chapter.corpus]
        return "".join(lines[chapter.start_line : chapter.end_line])

    def chapter_at(self, chapter_index: int) -> TocChapter:
        """Return the chapter at the given index, raising IndexError if out of range."""
        if chapter_index < 0 or chapter_index >= len(self.chapters):
            raise IndexError(chapter_index)
        return self.chapters[chapter_index]

    def search(self, query: str, limit: int = 8) -> list[TocSearchHit]:
        """Search the TOC by keyword using BM25. An empty query returns the first `limit` chapters."""
        query = query.strip().lower()
        if not query:
            return [
                TocSearchHit(
                    chapter_index=i,
                    corpus=ch.corpus,
                    heading=ch.heading,
                    heading_path=ch.heading_path,
                    subheadings=ch.subheadings,
                    score=0,
                )
                for i, ch in enumerate(self.chapters[:limit])
            ]

        raw_tokens = _split(query)
        content_tokens = [t for t in raw_tokens if t not in _STOP_WORDS]

        if content_tokens:
            stemmed = _stemmer.stemWords(content_tokens)
            stemmed = _expand_synonyms(stemmed)
            return self._ranked_search(
                stemmed, self._heading_token_sets, self._bm25_path, self._bm25_sub, limit,
            )

        # All tokens were stop words — search the full (unfiltered) indices
        stemmed = _stemmer.stemWords(raw_tokens)
        if not stemmed:
            return []
        return self._ranked_search(
            stemmed, self._heading_token_sets_full, self._bm25_path_full, self._bm25_sub_full, limit,
        )

    def _ranked_search(
        self,
        query_tokens: list[str],
        heading_sets: list[set[str]],
        bm25_path: BM25Okapi,
        bm25_sub: BM25Okapi,
        limit: int,
    ) -> list[TocSearchHit]:
        """Score each chapter using binary heading match + BM25 path/sub scores."""
        _H_WEIGHT = 10.0
        _P_WEIGHT = 6.0
        _S_WEIGHT = 3.0

        p_scores = bm25_path.get_scores(query_tokens)
        s_scores = bm25_sub.get_scores(query_tokens)
        qt = set(query_tokens)

        hits: list[TocSearchHit] = []
        for i, ch in enumerate(self.chapters):
            heading_match_count = len(qt & heading_sets[i])
            score = (
                _H_WEIGHT * heading_match_count
                + _P_WEIGHT * float(p_scores[i])
                + _S_WEIGHT * float(s_scores[i])
            )
            if score > 0:
                hits.append(
                    TocSearchHit(
                        chapter_index=i,
                        corpus=ch.corpus,
                        heading=ch.heading,
                        heading_path=ch.heading_path,
                        subheadings=ch.subheadings,
                        score=score,
                    )
                )

        hits.sort(key=lambda h: (-h.score, h.chapter_index))
        return hits[:limit]

    def render_toc(self) -> str:
        """Render a compact TOC string suitable for an LLM to pick from."""
        parts: list[str] = []
        current_corpus: CorpusName | None = None
        for idx, ch in enumerate(self.chapters):
            if ch.corpus != current_corpus:
                current_corpus = ch.corpus
                label = "NON-ZONING LAW" if current_corpus == "non_zoning" else "ZONING ORDINANCE"
                parts.append(f"--- {label} ---")
            heading = ch.heading
            subs = "; ".join(ch.subheadings[:40])
            if len(ch.subheadings) > 40:
                subs += f"; ... (+{len(ch.subheadings) - 40} more)"
            parts.append(
                f"[{idx}] {heading}\n"
                f"    Contains: {subs or '(no sub-sections)'}"
            )
        return "\n\n".join(parts)


def _chapter_level(corpus: CorpusName) -> int:
    """The heading level that defines a 'chapter' for each corpus.

    non_zoning uses ##### (level 5) for articles (e.g., ARTICLE 3. EXECUTIVE BRANCH).
    zoning uses #### (level 4) for sub-articles (e.g., 1.2 ADOPTION & EFFECT).
    """
    return 5 if corpus == "non_zoning" else 4


def parse_toc(markdown_text: str, corpus: CorpusName) -> tuple[list[TocChapter], list[str]]:
    """Parse a markdown file into chapter-level TOC entries."""
    lines = markdown_text.splitlines(keepends=True)
    chapter_level = _chapter_level(corpus)

    # First pass: find all chapter-level headings and their line positions.
    chapter_starts: list[tuple[int, str, int, list[str]]] = []  # (line, heading, level, heading_stack)
    heading_stack = [""] * 7

    for i, line in enumerate(lines):
        m = HEADING_RE.match(line.strip())
        if not m:
            continue
        level = len(m.group(1))
        text = m.group(2).strip()
        heading_stack[level] = text
        for j in range(level + 1, 7):
            heading_stack[j] = ""

        if level == chapter_level:
            path = [h for h in heading_stack[: level + 1] if h]
            chapter_starts.append((i, text, level, list(path)))

    # Second pass: for each chapter, collect subheadings.
    chapters: list[TocChapter] = []
    for ci, (start, heading, level, path) in enumerate(chapter_starts):
        end = chapter_starts[ci + 1][0] if ci + 1 < len(chapter_starts) else len(lines)

        subheadings: list[str] = []
        for line in lines[start + 1 : end]:
            m = HEADING_RE.match(line.strip())
            if m:
                sub_level = len(m.group(1))
                sub_text = m.group(2).strip()
                if sub_level > level and not sub_text.rstrip(".").endswith("Reserved"):
                    subheadings.append(sub_text)

        chapters.append(
            TocChapter(
                corpus=corpus,
                heading=heading,
                heading_path=tuple(path),
                level=level,
                start_line=start,
                end_line=end,
                subheadings=tuple(subheadings),
            )
        )

    return chapters, lines


def build_corpus_toc(
    non_zoning_text: str,
    zoning_text: str,
) -> CorpusToc:
    nz_chapters, nz_lines = parse_toc(non_zoning_text, "non_zoning")
    z_chapters, z_lines = parse_toc(zoning_text, "zoning")
    chapters = nz_chapters + z_chapters

    # Build BM25 indices for path and subheadings (with stop-word filtering)
    heading_token_sets = [set(_tokenize(ch.heading)) for ch in chapters]
    path_docs = [_tokenize(" ".join(ch.heading_path)) for ch in chapters]
    sub_docs = [_tokenize(" ".join(ch.subheadings)) for ch in chapters]

    # Fallback indices (no stop-word filtering) for all-stop-word queries
    heading_token_sets_full = [set(_tokenize_full(ch.heading)) for ch in chapters]
    path_docs_full = [_tokenize_full(" ".join(ch.heading_path)) for ch in chapters]
    sub_docs_full = [_tokenize_full(" ".join(ch.subheadings)) for ch in chapters]

    return CorpusToc(
        chapters=chapters,
        _lines_by_corpus={"non_zoning": nz_lines, "zoning": z_lines},
        _heading_token_sets=heading_token_sets,
        _bm25_path=BM25Okapi(path_docs),
        _bm25_sub=BM25Okapi(sub_docs),
        _heading_token_sets_full=heading_token_sets_full,
        _bm25_path_full=BM25Okapi(path_docs_full),
        _bm25_sub_full=BM25Okapi(sub_docs_full),
    )
