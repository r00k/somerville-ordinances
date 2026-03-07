from __future__ import annotations

from dataclasses import dataclass

from .types import CorpusName

ZONING_HINTS = {
    "zoning",
    "district",
    "setback",
    "lot",
    "overlay",
    "far",
    "floor area ratio",
    "special permit",
    "site plan",
    "inclusionary",
    "affordable housing",
    "use table",
    "building type",
    "demolition",
    "demolish",
    "parking ratio",
    "height limit",
}

NON_ZONING_HINTS = {
    "charter",
    "code of ordinances",
    "city council",
    "mayor",
    "city clerk",
    "traffic commission",
    "board of health",
    "petition",
    "ordinance committee",
    "municipal code",
    "elections",
}


@dataclass(frozen=True)
class RoutingDecision:
    corpus: CorpusName | None
    needs_clarification: bool
    clarification_question: str | None
    reason: str


def route_question(question: str) -> RoutingDecision:
    text = " ".join(question.lower().split())
    zoning_score = sum(1 for hint in ZONING_HINTS if hint in text)
    non_zoning_score = sum(1 for hint in NON_ZONING_HINTS if hint in text)

    if zoning_score and non_zoning_score:
        corpus: CorpusName = "zoning" if zoning_score >= non_zoning_score else "non_zoning"
        return RoutingDecision(
            corpus=corpus,
            needs_clarification=False,
            clarification_question=None,
            reason="mixed_signals_best_guess",
        )

    if zoning_score > 0:
        return RoutingDecision(
            corpus="zoning",
            needs_clarification=False,
            clarification_question=None,
            reason="zoning_signals",
        )

    if non_zoning_score > 0:
        return RoutingDecision(
            corpus="non_zoning",
            needs_clarification=False,
            clarification_question=None,
            reason="non_zoning_signals",
        )

    return RoutingDecision(
        corpus="non_zoning",
        needs_clarification=False,
        clarification_question=None,
        reason="no_routing_signals_default",
    )
