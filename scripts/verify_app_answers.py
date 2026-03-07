#!/usr/bin/env python3
"""Verification checks for critical Somerville-law QA answers.

This script asks the running app for three correctness-sensitive questions and
checks expected outcomes in the assistant response text.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class CheckCase:
    prompt: str
    expected_patterns: tuple[re.Pattern[str], ...]
    forbidden_patterns: tuple[re.Pattern[str], ...] = ()


CASES: tuple[CheckCase, ...] = (
    CheckCase(
        prompt="How many people sit on the Somerville city council?",
        expected_patterns=(
            re.compile(r"\b11\b"),
        ),
    ),
    CheckCase(
        prompt=(
            "What percentage of units must be affordable due to inclusionary zoning on projects "
            "with 2 units, and with 20 units? Also state the required number of affordable units for 20 units."
        ),
        expected_patterns=(
            re.compile(r"\b2\s*units?\b[^\n]{0,80}\b0\b"),
            re.compile(r"\b20\s*units?\b[^\n]{0,120}\b4\b"),
        ),
    ),
    CheckCase(
        prompt="Can you demolish a 100 year-old building in Somerville without permission?",
        expected_patterns=(
            re.compile(r"\bno\b", flags=re.IGNORECASE),
        ),
        forbidden_patterns=(
            re.compile(r"\byes\b", flags=re.IGNORECASE),
        ),
    ),
)


def ask(base_url: str, message: str, history: list[dict[str, str]]) -> dict[str, object]:
    response = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={"message": message, "history": history},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run critical-answer verification checks against /api/chat")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Base URL for the running app")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    history: list[dict[str, str]] = []

    failures = 0
    for idx, case in enumerate(CASES, start=1):
        try:
            payload = ask(args.base_url, case.prompt, history)
        except Exception as exc:
            print(f"[error] Case {idx}: request failed: {exc}")
            return 1

        answer = str(payload.get("answer", ""))
        history.append({"role": "user", "content": case.prompt})
        history.append({"role": "assistant", "content": answer})

        missing = [pattern.pattern for pattern in case.expected_patterns if not pattern.search(answer)]
        forbidden = [pattern.pattern for pattern in case.forbidden_patterns if pattern.search(answer)]

        if payload.get("refused"):
            failures += 1
            print(f"[fail] Case {idx}: app refused to answer.")
            print(f"       Prompt: {case.prompt}")
            continue

        if missing or forbidden:
            failures += 1
            print(f"[fail] Case {idx}")
            print(f"       Prompt: {case.prompt}")
            if missing:
                print(f"       Missing patterns: {missing}")
            if forbidden:
                print(f"       Forbidden patterns present: {forbidden}")
            print(f"       Answer: {answer}\n")
            continue

        print(f"[ok] Case {idx} passed")

    if failures:
        print(f"[error] Verification failed: {failures} case(s)")
        return 1

    print("[ok] All verification checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
