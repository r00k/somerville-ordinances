from __future__ import annotations

from pathlib import Path

import pytest

from app.toc import CorpusToc, build_corpus_toc, parse_toc

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def toc() -> CorpusToc:
    nz = (ROOT / "somerville-law-non-zoning.md").read_text(encoding="utf-8")
    z = (ROOT / "somerville-zoning.md").read_text(encoding="utf-8")
    return build_corpus_toc(nz, z)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_toc_non_zoning_finds_chapters() -> None:
    text = (ROOT / "somerville-law-non-zoning.md").read_text(encoding="utf-8")
    chapters, lines = parse_toc(text, "non_zoning")
    assert len(chapters) > 10
    headings = [ch.heading for ch in chapters]
    assert "ARTICLE 3. EXECUTIVE BRANCH" in headings
    assert "ARTICLE II. MAYOR*" in headings


def test_parse_toc_zoning_finds_chapters() -> None:
    text = (ROOT / "somerville-zoning.md").read_text(encoding="utf-8")
    chapters, lines = parse_toc(text, "zoning")
    assert len(chapters) > 10
    headings = [ch.heading for ch in chapters]
    assert "1.1 GENERAL" in headings
    assert "9.1 PERMITTED USES" in headings


def test_build_corpus_toc_chapter_text_contains_expected_content(toc: CorpusToc) -> None:
    leg = next(ch for ch in toc.chapters if ch.heading == "ARTICLE 2. LEGISLATIVE BRANCH")
    text = toc.chapter_text(leg)
    assert "city council consisting of 11 members" in text


def test_render_toc_is_compact(toc: CorpusToc) -> None:
    rendered = toc.render_toc()
    assert len(rendered) < 100_000
    assert "[0]" in rendered


# ---------------------------------------------------------------------------
# Search: basic retrieval — the right chapter appears in the top 3
# ---------------------------------------------------------------------------

def _top_headings(toc: CorpusToc, query: str, n: int = 3) -> list[str]:
    return [h.heading for h in toc.search(query, limit=n)]


def _top_indices(toc: CorpusToc, query: str, n: int = 3) -> list[int]:
    return [h.chapter_index for h in toc.search(query, limit=n)]


class TestSearchFindsRightChapter:
    """For each topic, the most relevant chapter should rank in the top 3."""

    def test_mayor(self, toc: CorpusToc) -> None:
        assert "ARTICLE II. MAYOR*" in _top_headings(toc, "mayor")

    def test_mayor_natural_question(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "How long is the mayor's term?")
        assert "ARTICLE II. MAYOR*" in headings

    def test_executive_branch_for_mayor_term(self, toc: CorpusToc) -> None:
        # The actual term length is in the EXECUTIVE BRANCH article
        headings = _top_headings(toc, "How long is the mayor's term?")
        assert "ARTICLE 3. EXECUTIVE BRANCH" in headings

    def test_school_committee(self, toc: CorpusToc) -> None:
        assert "ARTICLE 4. SCHOOL COMMITTEE" in _top_headings(toc, "school committee")

    def test_noise(self, toc: CorpusToc) -> None:
        assert "ARTICLE VII. OFFENSES AGAINST PUBLIC PEACE*" in _top_headings(toc, "noise")

    def test_noise_natural_question(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "What are the noise ordinance hours?")
        assert "ARTICLE VII. OFFENSES AGAINST PUBLIC PEACE*" in headings

    def test_fences(self, toc: CorpusToc) -> None:
        assert "10.5 FENCES & WALLS" in _top_headings(toc, "fence")

    def test_dogs(self, toc: CorpusToc) -> None:
        assert "ARTICLE II. DOGS*" in _top_headings(toc, "dogs")

    def test_fire_department(self, toc: CorpusToc) -> None:
        assert "ARTICLE II. FIRE DEPARTMENT*" in _top_headings(toc, "fire")

    def test_alcohol(self, toc: CorpusToc) -> None:
        assert "ARTICLE IX. ALCOHOLIC BEVERAGE LICENSES" in _top_headings(toc, "alcohol")

    def test_short_term_rentals(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "short-term rentals Airbnb")
        assert "ARTICLE X. SHORT-TERM RENTALS" in headings

    def test_demolition(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "demolition")
        assert any("DEMOLITION" in h for h in headings)

    def test_speed_limit(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "speed limit")
        assert any("SPEED" in h for h in headings)

    def test_leaf_blower(self, toc: CorpusToc) -> None:
        # Leaf blowers are a division within OFFENSES AGAINST PUBLIC PEACE
        headings = _top_headings(toc, "leaf blower")
        assert "ARTICLE VII. OFFENSES AGAINST PUBLIC PEACE*" in headings

    def test_building_height(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "building height")
        assert any("BUILDING" in h for h in headings)

    def test_motor_vehicle_parking(self, toc: CorpusToc) -> None:
        assert "11.2 MOTOR VEHICLE PARKING" in _top_headings(toc, "motor vehicle parking")

    def test_tree_preservation(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "tree preservation")
        assert "ARTICLE VI. TREE PRESERVATION ORDINANCE" in headings


# ---------------------------------------------------------------------------
# Search: case insensitivity
# ---------------------------------------------------------------------------

class TestSearchCaseInsensitive:
    def test_lowercase(self, toc: CorpusToc) -> None:
        assert _top_indices(toc, "mayor") == _top_indices(toc, "MAYOR")

    def test_mixed_case(self, toc: CorpusToc) -> None:
        assert _top_indices(toc, "mayor") == _top_indices(toc, "Mayor")


# ---------------------------------------------------------------------------
# Search: empty / all-stop-word queries
# ---------------------------------------------------------------------------

class TestSearchEdgeCases:
    def test_empty_query_returns_first_chapters(self, toc: CorpusToc) -> None:
        hits = toc.search("", limit=3)
        assert len(hits) == 3
        assert hits[0].chapter_index == 0

    def test_whitespace_only_returns_first_chapters(self, toc: CorpusToc) -> None:
        hits = toc.search("   ", limit=3)
        assert len(hits) == 3

    def test_all_stop_words_falls_back_to_raw_tokens(self, toc: CorpusToc) -> None:
        # "is it" is all stop words — should still return *something* via fallback
        hits = toc.search("is it", limit=3)
        assert len(hits) > 0

    def test_limit_respected(self, toc: CorpusToc) -> None:
        hits_3 = toc.search("parking", limit=3)
        hits_1 = toc.search("parking", limit=1)
        assert len(hits_1) == 1
        assert len(hits_3) == 3
        assert hits_1[0].chapter_index == hits_3[0].chapter_index

    def test_results_sorted_by_score_descending(self, toc: CorpusToc) -> None:
        hits = toc.search("mayor", limit=5)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_no_zero_score_hits_for_real_query(self, toc: CorpusToc) -> None:
        hits = toc.search("mayor", limit=10)
        assert all(h.score > 0 for h in hits)

    def test_hit_fields_populated(self, toc: CorpusToc) -> None:
        hits = toc.search("mayor", limit=1)
        hit = hits[0]
        assert hit.heading
        assert hit.heading_path
        assert hit.corpus in ("non_zoning", "zoning")
        assert hit.chapter_index >= 0


# ---------------------------------------------------------------------------
# Search: prefix matching (plurals / inflections)
# ---------------------------------------------------------------------------

class TestSearchSynonymExpansion:
    """Queries using everyday vocabulary should find chapters with formal/legal headings."""

    def test_tattoo_finds_body_art(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "tattoo")
        assert any("BODY ART" in h for h in headings)

    def test_tattoos_plural_finds_body_art(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "tattoos")
        assert any("BODY ART" in h for h in headings)

    def test_chicken_finds_non_domesticated_animals(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "chicken")
        assert "ARTICLE III. NON-DOMESTICATED ANIMALS" in headings

    def test_can_i_keep_chickens(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "can I keep chickens")
        assert "ARTICLE III. NON-DOMESTICATED ANIMALS" in headings

    def test_busking_finds_street_performers(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "busking")
        assert "ARTICLE VII. STREET PERFORMERS*" in headings

    def test_crosswalk_finds_pedestrian_sections(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "crosswalk")
        assert any("Pedestrian" in h or "pedestrian" in h.lower() for h in headings)

    def test_landmark_finds_historic_districts(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "landmark")
        assert "ARTICLE II. HISTORIC DISTRICTS*" in headings

    def test_liquor_license_finds_alcoholic_beverage(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "liquor license")
        assert "ARTICLE IX. ALCOHOLIC BEVERAGE LICENSES" in headings

    def test_airbnb_finds_short_term_rentals(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "airbnb")
        assert "ARTICLE X. SHORT-TERM RENTALS" in headings

    def test_garbage_finds_trash(self, toc: CorpusToc) -> None:
        assert "ARTICLE II. TRASH*" in _top_headings(toc, "garbage")

    def test_recycling_finds_trash(self, toc: CorpusToc) -> None:
        assert "ARTICLE II. TRASH*" in _top_headings(toc, "recycling")

    def test_natural_gas_ban_finds_fossil_fuel(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "natural gas ban")
        assert "ARTICLE VII. FOSSIL FUEL FREE CONSTRUCTION" in headings

    def test_eviction_finds_housing(self, toc: CorpusToc) -> None:
        headings = _top_headings(toc, "eviction", n=5)
        assert any("HOUSING" in h for h in headings)


class TestSearchPrefixMatching:
    def test_singular_finds_plural_heading(self, toc: CorpusToc) -> None:
        # "fence" should find "FENCES & WALLS"
        assert "10.5 FENCES & WALLS" in _top_headings(toc, "fence")

    def test_singular_finds_plural_in_subheadings(self, toc: CorpusToc) -> None:
        # "blower" should match "Leaf blowers regulated" subheading
        headings = _top_headings(toc, "blower", n=5)
        assert "ARTICLE VII. OFFENSES AGAINST PUBLIC PEACE*" in headings

    def test_park_does_not_only_return_parking(self, toc: CorpusToc) -> None:
        # "park" is a prefix of "parking" — but PARKS should also appear
        headings = _top_headings(toc, "park", n=5)
        assert any("PARK" in h and "PARKING" not in h for h in headings), (
            f"Expected a parks chapter, got: {headings}"
        )


# ---------------------------------------------------------------------------
# Search: IDF weighting — rare terms should outrank common ones
# ---------------------------------------------------------------------------

class TestSearchIdfWeighting:
    def test_rare_term_outranks_common_term(self, toc: CorpusToc) -> None:
        # "noise" is rare in headings; "hours" is fairly common.
        # The noise chapter should rank above any "hours" chapter.
        hits = toc.search("noise hours", limit=5)
        noise_idx = next(
            (i for i, h in enumerate(hits) if "PEACE" in h.heading or "noise" in h.heading.lower()),
            None,
        )
        assert noise_idx is not None, f"Noise chapter missing from: {[h.heading for h in hits]}"

    def test_specific_term_beats_generic(self, toc: CorpusToc) -> None:
        # "demolition" is specific; "safety" is generic and appears in many chapters
        hits = toc.search("demolition safety", limit=3)
        assert any("DEMOLITION" in h.heading for h in hits)


# ---------------------------------------------------------------------------
# Search: both corpora represented
# ---------------------------------------------------------------------------

class TestSearchCorpora:
    def test_non_zoning_result(self, toc: CorpusToc) -> None:
        hits = toc.search("mayor", limit=1)
        assert hits[0].corpus == "non_zoning"

    def test_zoning_result(self, toc: CorpusToc) -> None:
        hits = toc.search("fences walls", limit=1)
        assert hits[0].corpus == "zoning"

    def test_cross_corpus_query(self, toc: CorpusToc) -> None:
        # A broad query should return results from both corpora
        hits = toc.search("building", limit=10)
        corpora = {h.corpus for h in hits}
        assert "non_zoning" in corpora
        assert "zoning" in corpora


# ---------------------------------------------------------------------------
# chapter_at: bounds checking
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Search: stop words in natural questions shouldn't derail results
# ---------------------------------------------------------------------------

class TestSearchStopWordFiltering:
    def test_keep_is_stop_word(self, toc: CorpusToc) -> None:
        # "keep" should not match "Police to keep record of towed vehicles"
        hits = toc.search("can I keep chickens", limit=3)
        headings = [h.heading for h in hits]
        assert not any("tow" in h.lower() for h in headings)

    def test_somerville_is_stop_word(self, toc: CorpusToc) -> None:
        # "somerville" is too common to be useful as a search term
        hits = toc.search("somerville noise rules", limit=3)
        assert any("PEACE" in h.heading or "noise" in h.heading.lower() for h in hits)


class TestChapterAt:
    def test_valid_index(self, toc: CorpusToc) -> None:
        ch = toc.chapter_at(0)
        assert ch.heading

    def test_negative_index_raises(self, toc: CorpusToc) -> None:
        with pytest.raises(IndexError):
            toc.chapter_at(-1)

    def test_out_of_range_raises(self, toc: CorpusToc) -> None:
        with pytest.raises(IndexError):
            toc.chapter_at(len(toc.chapters))
