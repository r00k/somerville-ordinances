"""Parse markdown corpus files into a table of contents with chapter-level text extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    # Domain-specific: too common in municipal law headings to be useful
    "ordinance", "city", "street", "section", "article",
})


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

    def chapter_text(self, chapter: TocChapter) -> str:
        lines = self._lines_by_corpus[chapter.corpus]
        return "".join(lines[chapter.start_line : chapter.end_line])

    def chapter_at(self, chapter_index: int) -> TocChapter:
        """Return the chapter at the given index, raising IndexError if out of range."""
        if chapter_index < 0 or chapter_index >= len(self.chapters):
            raise IndexError(chapter_index)
        return self.chapters[chapter_index]

    def search(self, query: str, limit: int = 8) -> list[TocSearchHit]:
        """Search the TOC by keyword. An empty query returns the first `limit` chapters."""
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

        all_tokens = [t for t in re.split(r"\W+", query) if len(t) > 1]
        content_terms = [t for t in all_tokens if t not in _STOP_WORDS]
        # Fall back to all tokens if stop-word filtering removes everything
        search_terms = content_terms or all_tokens

        # Pre-compute per-chapter searchable text
        chapter_texts = []
        for ch in self.chapters:
            heading = ch.heading.lower()
            path = " ".join(ch.heading_path).lower()
            subs = " ".join(ch.subheadings).lower()
            chapter_texts.append((heading, path, subs))

        # IDF-like weight: terms matching fewer chapters score higher
        term_weights: dict[str, float] = {}
        for term in search_terms:
            pattern = r"\b" + re.escape(term)
            doc_freq = sum(
                1 for heading, path, subs in chapter_texts
                if re.search(pattern, heading + " " + path + " " + subs)
            )
            # Rarer terms get higher weight; terms in 1 chapter → ~3x, in 100+ → ~1x
            term_weights[term] = max(1.0, 5.0 - (doc_freq / len(self.chapters)) * 20)

        hits: list[TocSearchHit] = []

        for i, (heading, path, subs) in enumerate(chapter_texts):
            searchable = heading + " " + path + " " + subs

            score = 0.0
            for term in search_terms:
                w = term_weights[term]
                # Prefix match to handle plurals/inflections (e.g. fence→fences)
                pattern = r"\b" + re.escape(term)
                if re.search(pattern, heading):
                    score += 10 * w
                if re.search(pattern, path):
                    score += 6 * w
                if re.search(pattern, subs):
                    score += 3 * w

            # Bonus for matching the full query as a phrase
            if query in searchable:
                score += 20

            if score > 0:
                ch = self.chapters[i]
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

    return CorpusToc(
        chapters=nz_chapters + z_chapters,
        _lines_by_corpus={"non_zoning": nz_lines, "zoning": z_lines},
    )
