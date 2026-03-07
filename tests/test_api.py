from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import build_runtime, create_app
from app.config import AppSettings
from app.observability import LOGGER_NAME
from app.qa import AnswerEngine

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("MODEL_API_KEY", "")


@pytest.fixture(scope="session")
def client() -> TestClient:
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY not set")
    root = Path(__file__).resolve().parent.parent
    settings = AppSettings(
        non_zoning_markdown=root / "somerville-law-non-zoning.md",
        zoning_markdown=root / "somerville-zoning.md",
        model_provider="openai",
        model_name="gpt-5.4",
        model_api_key=OPENAI_API_KEY,
        model_base_url=None,
        request_timeout_seconds=60.0,
        retrieval_top_k=10,
        retrieval_excerpt_chars=1800,
        retrieval_min_score=0.0,
        max_history_messages=8,
        enable_long_context_verification=False,
        long_context_top_k=18,
        long_context_trigger_min_confidence="medium",
        observability_log_level="INFO",
    )
    runtime = build_runtime(settings)
    app = create_app(runtime)
    return TestClient(app)


def _parse_structured_events(stderr: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stderr.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "event" not in payload:
            continue
        events.append(payload)
    return events


def _capture_observability_messages() -> tuple[logging.Logger, logging.Handler, list[str]]:
    logger = logging.getLogger(LOGGER_NAME)
    messages: list[str] = []

    class MessageCaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = MessageCaptureHandler()
    logger.addHandler(handler)
    return logger, handler, messages


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["sections_loaded"] > 3000


@pytest.mark.parametrize(
    "prompt",
    [
        "How many people sit on the Somerville city council?",
        "Can you demolish a 100 year-old building in Somerville without permission?",
        "How long is the mayor's term?",
    ],
)
def test_critical_questions(
    client: TestClient,
    prompt: str,
) -> None:
    response = client.post(
        "/api/chat",
        json={"message": prompt, "history": []},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["refused"] is False
    assert len(payload["answer"]) > 20
    assert payload["citations"]


def test_chat_logs_question_attempt_and_response_as_json(
    client: TestClient,
) -> None:
    prompt = "How many people sit on the Somerville city council?"
    logger, handler, messages = _capture_observability_messages()
    try:
        response = client.post("/api/chat", json={"message": prompt, "history": []})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200

    events = _parse_structured_events("\n".join(messages))
    assert events

    request_event = next(event for event in events if event["event"] == "chat.request_received")
    response_event = next(event for event in events if event["event"] == "chat.response_emitted")
    qa_event = next(event for event in events if event["event"] == "qa.assistance_attempt_started")

    assert request_event["question"] == prompt
    assert qa_event["request_id"] == request_event["request_id"]
    assert response_event["request_id"] == request_event["request_id"]
    assert response_event["response_status"] == 200
    assert response_event["response"]["answer"]


def test_chat_pipeline_error_logs_structured_json(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: AnswerEngine, question: str, history: list[dict[str, str]], request_id: str | None = None):
        del self, question, history, request_id
        raise RuntimeError("forced failure")

    monkeypatch.setattr(AnswerEngine, "ask", boom)
    logger, handler, messages = _capture_observability_messages()
    try:
        response = client.post("/api/chat", json={"message": "trigger pipeline failure", "history": []})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 500

    events = _parse_structured_events("\n".join(messages))
    failure_event = next(event for event in events if event["event"] == "chat.request_failed")

    assert failure_event["response_status"] == 500
    assert failure_event["error"]["type"] == "RuntimeError"
    assert "forced failure" in failure_event["error"]["message"]
