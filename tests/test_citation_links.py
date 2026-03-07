from __future__ import annotations

from pathlib import Path

from app.citation_links import build_citation_links
from app.config import AppSettings
from app.types import CorpusSection


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        non_zoning_markdown=tmp_path / "non-zoning.md",
        zoning_markdown=tmp_path / "zoning.md",
        non_zoning_readable_html=tmp_path / "non-zoning.readable.html",
        zoning_readable_html=tmp_path / "zoning.readable.html",
        model_provider="mock",
        model_name="mock-local",
        model_api_key=None,
        model_base_url=None,
        request_timeout_seconds=30.0,
        retrieval_top_k=5,
        retrieval_excerpt_chars=900,
        retrieval_min_score=0.0,
        max_history_messages=4,
        enable_long_context_verification=False,
        long_context_top_k=8,
        long_context_trigger_min_confidence="medium",
        observability_log_level="INFO",
    )


def test_build_citation_links_prefers_local_readable_html(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.non_zoning_readable_html.write_text("<html></html>", encoding="utf-8")

    section = CorpusSection(
        corpus="non_zoning",
        secid="2588",
        tocid="001.003",
        heading="Section 1-1: Incorporation",
        heading_path=("PART I", "ARTICLE 1", "Section 1-1"),
        source_file="somerville-law-non-zoning.md",
        ordinal=0,
        text="The residents of the city of Somerville...",
    )

    links = build_citation_links(section, settings)

    assert links.url == "/documents/non-zoning#secid-2588"
    assert links.local_url == "/documents/non-zoning#secid-2588"
    assert links.official_url == "https://online.encodeplus.com/regs/somerville-ma-coo/doc-viewer.aspx#secid-2588"


def test_build_citation_links_falls_back_to_official_when_local_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    section = CorpusSection(
        corpus="zoning",
        secid="442",
        tocid="001",
        heading="1.1.10 Text & Graphics",
        heading_path=("1. INTRODUCTORY PROVISIONS", "1.1 GENERAL", "1.1.10 Text & Graphics"),
        source_file="somerville-zoning.md",
        ordinal=0,
        text="In the case of a conflict between text and photos, the text governs.",
    )

    links = build_citation_links(section, settings)

    assert links.local_url is None
    assert links.url == "https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx#secid-442"
    assert links.official_url == "https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx#secid-442"
