from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import build_runtime, create_app
from app.config import AppSettings


@pytest.fixture(scope="session")
def client() -> TestClient:
    root = Path(__file__).resolve().parent.parent
    settings = AppSettings(
        non_zoning_markdown=root / "somerville-law-non-zoning.md",
        zoning_markdown=root / "somerville-zoning.md",
        model_provider="mock",
        model_name="mock-local",
        model_api_key=None,
        model_base_url=None,
        request_timeout_seconds=30.0,
        retrieval_top_k=10,
        retrieval_excerpt_chars=1800,
        retrieval_min_score=0.0,
        max_history_messages=8,
        enable_long_context_verification=False,
        long_context_top_k=18,
        long_context_trigger_min_confidence="medium",
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["sections_loaded"] > 3000


@pytest.mark.parametrize(
    "prompt,patterns,expected_confidence",
    [
        ("How many people sit on the Somerville city council?", [r"\b11\b"], "high"),
        (
            "What percentage of units must be affordable due to inclusionary zoning on projects with 2 units and 20 units?",
            [r"\b2\s*units?\b[^\n]{0,80}\b0\b", r"\b20\s*units?\b[^\n]{0,120}\b4\b"],
            "medium",
        ),
        (
            "Can you demolish a 100 year-old building in Somerville without permission?",
            [r"\bno\b"],
            "medium",
        ),
    ],
)
def test_critical_questions(
    client: TestClient,
    prompt: str,
    patterns: list[str],
    expected_confidence: str,
) -> None:
    response = client.post(
        "/api/chat",
        json={"message": prompt, "history": []},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["refused"] is False
    assert payload["confidence"] == expected_confidence
    answer = payload["answer"]
    for pattern in patterns:
        assert re.search(pattern, answer, flags=re.IGNORECASE), answer
