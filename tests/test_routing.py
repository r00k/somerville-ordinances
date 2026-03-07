from __future__ import annotations

from app.routing import route_question


def test_routes_clear_zoning_question() -> None:
    decision = route_question("What is the minimum front setback in this zoning district?")
    assert decision.corpus == "zoning"
    assert decision.needs_clarification is False


def test_routes_clear_non_zoning_question() -> None:
    decision = route_question("How many people sit on the city council?")
    assert decision.corpus == "non_zoning"
    assert decision.needs_clarification is False


def test_routes_mixed_question_picks_best_guess() -> None:
    decision = route_question(
        "What are zoning setback rules and what vote is required for a city council veto override?"
    )
    assert decision.corpus is not None
    assert decision.needs_clarification is False
    assert decision.reason == "mixed_signals_best_guess"


def test_routes_no_signal_question_defaults_to_non_zoning() -> None:
    decision = route_question("What permit do I need?")
    assert decision.corpus == "non_zoning"
    assert decision.needs_clarification is False
