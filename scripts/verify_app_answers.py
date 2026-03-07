#!/usr/bin/env python3
"""Verification checks for Somerville-law QA answers.

This script asks the running app correctness-sensitive questions and checks
expected outcomes in the assistant response text.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Literal

import requests


def ci(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, flags=re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class CheckCase:
    name: str
    prompt: str
    expected_patterns: tuple[re.Pattern[str], ...]
    expected_corpus: Literal["non_zoning", "zoning"] | None = None
    forbidden_patterns: tuple[re.Pattern[str], ...] = ()
    min_citations: int = 1


CRITICAL_CASES: tuple[CheckCase, ...] = (
    CheckCase(
        name="city-council-size",
        prompt="How many people sit on the Somerville city council?",
        expected_patterns=(
            ci(r"\b(?:11|eleven)\b"),
        ),
        expected_corpus="non_zoning",
    ),
    CheckCase(
        name="inclusionary-2-vs-20-units",
        prompt=(
            "What percentage of units must be affordable due to inclusionary zoning on projects "
            "with 2 units, and with 20 units? Also state the required number of affordable units for 20 units."
        ),
        expected_patterns=(
            ci(r"\b2(?:\s*|-)?units?\b.{0,220}\b0\b|\b0\b.{0,220}\b2(?:\s*|-)?units?\b"),
            ci(r"\b20(?:\s*|-)?units?\b.{0,260}\b4\b|\b4\b.{0,260}\b20(?:\s*|-)?units?\b"),
        ),
        expected_corpus="zoning",
    ),
    CheckCase(
        name="demolition-no-without-permission",
        prompt="Can you demolish a 100 year-old building in Somerville without permission?",
        expected_patterns=(
            ci(r"\bno\b"),
        ),
        forbidden_patterns=(
            ci(r"\byes\b"),
        ),
        expected_corpus="zoning",
    ),
)


SANITY_CASES: tuple[CheckCase, ...] = (
    CheckCase(
        name="mayor-term",
        prompt="What is the term of office for the mayor of Somerville?",
        expected_patterns=(
            ci(r"\b(?:2|two)\s+years?\b"),
            ci(r"\bmayor\b"),
        ),
        expected_corpus="non_zoning",
    ),
    CheckCase(
        name="ward-councilor-count",
        prompt="How many ward councilors are on the Somerville city council?",
        expected_patterns=(
            ci(r"(?:\b(?:7|seven)\b.*?\bward\s+council(?:or|ors)\b|\bward\s+council(?:or|ors)\b.*?\b(?:7|seven)\b)"),
        ),
        expected_corpus="non_zoning",
    ),
    CheckCase(
        name="veto-override-votes",
        prompt="How many votes are needed for the city council to override a mayoral veto?",
        expected_patterns=(
            ci(r"\b(?:8|eight)\b.*?\bmembers?\b"),
            ci(r"\boverride\b|\bveto\b"),
        ),
        expected_corpus="non_zoning",
    ),
    CheckCase(
        name="special-meeting-notice-days",
        prompt=(
            "For a special city council meeting called by the president (non-emergency), "
            "how many business days in advance must notice be delivered to the city clerk?"
        ),
        expected_patterns=(
            ci(r"\b(?:3|three)\s+business\s+days?\b"),
        ),
        expected_corpus="non_zoning",
    ),
    CheckCase(
        name="group-petition-threshold",
        prompt=(
            "How many voters must sign a petition for the city council to hold a public hearing "
            "under the charter petition process?"
        ),
        expected_patterns=(
            ci(r"\b(?:50|fifty)\s+voters?\b"),
        ),
        expected_corpus="non_zoning",
    ),
    CheckCase(
        name="zoning-text-vs-graphics",
        prompt="In the zoning ordinance, if the text conflicts with a figure or photo, which controls?",
        expected_patterns=(
            ci(r"\btext\b.*?\b(?:controls?|governs?)\b"),
        ),
        expected_corpus="zoning",
    ),
    CheckCase(
        name="demolition-definition-threshold",
        prompt="How does the zoning ordinance define demolition in terms of how much of the exterior is removed?",
        expected_patterns=(
            ci(r"\b(?:50|fifty)\s*(?:%|percent)\b"),
            ci(r"\bwalls?\b.*?\broof\b|\broof\b.*?\bwalls?\b"),
        ),
        expected_corpus="zoning",
    ),
    CheckCase(
        name="nr-address-sign-height",
        prompt="How tall can an address sign be under Neighborhood Residence sign standards?",
        expected_patterns=(
            ci(r"\b(?:12|twelve)(?:\s*\(\s*12\s*\))?\s*(?:inch|inches)\b|\b(?:12|twelve)\s*(?:inch|inches)\b"),
        ),
        expected_corpus="zoning",
    ),
    CheckCase(
        name="unbundled-parking",
        prompt="Does Somerville zoning require motor vehicle parking spaces to be unbundled from housing costs?",
        expected_patterns=(
            ci(r"\bunbundled\b"),
        ),
        forbidden_patterns=(
            ci(r"\b(?:not\s+required|no)\b.*?\bunbundled\b"),
        ),
        expected_corpus="zoning",
    ),
    CheckCase(
        name="unmapped-land-default-district",
        prompt="If land is not mapped into any zoning district, how is it classified by default?",
        expected_patterns=(
            ci(r"\bcivic\s+district\b"),
        ),
        expected_corpus="zoning",
    ),
)


def resolve_cases(suite: Literal["critical", "sanity", "all"]) -> tuple[CheckCase, ...]:
    if suite == "critical":
        return CRITICAL_CASES
    if suite == "sanity":
        return SANITY_CASES
    return CRITICAL_CASES + SANITY_CASES


def ask(base_url: str, message: str, history: list[dict[str, str]], timeout: float) -> dict[str, object]:
    response = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={"message": message, "history": history},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run answer verification checks against /api/chat")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Base URL for the running app")
    parser.add_argument(
        "--suite",
        choices=("critical", "sanity", "all"),
        default="critical",
        help="Which verification suite to run (default: critical).",
    )
    parser.add_argument(
        "--carry-history",
        action="store_true",
        help="Carry chat history across checks (default: each case runs independently).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-request timeout in seconds (default: 120).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = resolve_cases(args.suite)
    history: list[dict[str, str]] = []

    failures = 0
    for idx, case in enumerate(cases, start=1):
        case_history = history if args.carry_history else []
        try:
            payload = ask(args.base_url, case.prompt, case_history, args.timeout)
        except Exception as exc:
            print(f"[error] Case {idx}: request failed: {exc}")
            return 1

        answer = str(payload.get("answer", ""))
        if args.carry_history:
            history.append({"role": "user", "content": case.prompt})
            history.append({"role": "assistant", "content": answer})

        missing = [pattern.pattern for pattern in case.expected_patterns if not pattern.search(answer)]
        forbidden = [pattern.pattern for pattern in case.forbidden_patterns if pattern.search(answer)]

        citations = payload.get("citations")
        citation_count = len(citations) if isinstance(citations, list) else 0
        routed_corpus = payload.get("routed_corpus")

        if payload.get("refused"):
            failures += 1
            print(f"[fail] Case {idx} ({case.name}): app refused to answer.")
            print(f"       Prompt: {case.prompt}")
            continue

        if case.expected_corpus and routed_corpus != case.expected_corpus:
            failures += 1
            print(f"[fail] Case {idx} ({case.name})")
            print(f"       Prompt: {case.prompt}")
            print(f"       Expected routed_corpus={case.expected_corpus}, got {routed_corpus}")
            continue

        if citation_count < case.min_citations:
            failures += 1
            print(f"[fail] Case {idx} ({case.name})")
            print(f"       Prompt: {case.prompt}")
            print(f"       Expected at least {case.min_citations} citation(s), got {citation_count}")
            print(f"       Answer: {answer}\n")
            continue

        if isinstance(citations, list) and routed_corpus:
            bad_corpus_citations = [
                citation for citation in citations if isinstance(citation, dict) and citation.get("corpus") != routed_corpus
            ]
            if bad_corpus_citations:
                failures += 1
                print(f"[fail] Case {idx} ({case.name})")
                print(f"       Prompt: {case.prompt}")
                print(f"       Citation corpus mismatch with routed corpus {routed_corpus}")
                continue

        if missing or forbidden:
            failures += 1
            print(f"[fail] Case {idx} ({case.name})")
            print(f"       Prompt: {case.prompt}")
            if missing:
                print(f"       Missing patterns: {missing}")
            if forbidden:
                print(f"       Forbidden patterns present: {forbidden}")
            print(f"       Answer: {answer}\n")
            continue

        print(f"[ok] Case {idx} ({case.name}) passed - citations={citation_count}")

    if failures:
        print(f"[error] Verification failed: {failures} case(s)")
        return 1

    print(f"[ok] All {len(cases)} verification checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
