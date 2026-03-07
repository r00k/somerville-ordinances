from __future__ import annotations

from pathlib import Path

from app.toc import build_corpus_toc, parse_toc


def test_parse_toc_non_zoning_finds_chapters() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "somerville-law-non-zoning.md").read_text(encoding="utf-8")
    chapters, lines = parse_toc(text, "non_zoning")
    assert len(chapters) > 10
    headings = [ch.heading for ch in chapters]
    assert "DIVISION 1 CHARTER" in headings
    assert "CHAPTER 2 ADMINISTRATION*" in headings


def test_parse_toc_zoning_finds_chapters() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "somerville-zoning.md").read_text(encoding="utf-8")
    chapters, lines = parse_toc(text, "zoning")
    assert len(chapters) > 10
    headings = [ch.heading for ch in chapters]
    assert "1. INTRODUCTORY PROVISIONS" in headings
    assert "9. USE PROVISIONS" in headings


def test_build_corpus_toc_chapter_text_contains_expected_content() -> None:
    root = Path(__file__).resolve().parent.parent
    nz = (root / "somerville-law-non-zoning.md").read_text(encoding="utf-8")
    z = (root / "somerville-zoning.md").read_text(encoding="utf-8")
    toc = build_corpus_toc(nz, z)

    # The charter division should contain the "11 members" city council text
    charter = next(ch for ch in toc.chapters if ch.heading == "DIVISION 1 CHARTER")
    text = toc.chapter_text(charter)
    assert "city council consisting of 11 members" in text


def test_render_toc_is_compact() -> None:
    root = Path(__file__).resolve().parent.parent
    nz = (root / "somerville-law-non-zoning.md").read_text(encoding="utf-8")
    z = (root / "somerville-zoning.md").read_text(encoding="utf-8")
    toc = build_corpus_toc(nz, z)
    rendered = toc.render_toc()
    # Should be well under 100KB — manageable for a single LLM call
    assert len(rendered) < 100_000
    assert "[0]" in rendered
