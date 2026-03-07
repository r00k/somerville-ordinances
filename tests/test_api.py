from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import build_runtime, create_app
from app.config import AppSettings
from app.observability import LOGGER_NAME
from app.two_pass import TwoPassEngine

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("MODEL_API_KEY", "")


@pytest.fixture(scope="session")
def client() -> TestClient:
    if not OPENAI_API_KEY:
        pytest.skip("OPENAI_API_KEY not set")
    root = Path(__file__).resolve().parent.parent
    settings = AppSettings(
        non_zoning_markdown=root / "somerville-law-non-zoning.md",
        zoning_markdown=root / "somerville-zoning.md",
        non_zoning_readable_html=root / "somerville-law-non-zoning.readable.html",
        zoning_readable_html=root / "somerville-zoning.readable.html",
        model_provider="openai",
        model_name="gpt-5.4",
        pass1_model_name="gpt-4.1-mini",
        model_api_key=OPENAI_API_KEY,
        model_base_url=None,
        request_timeout_seconds=60.0,
        max_history_messages=8,
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
    assert payload["chapters_loaded"] > 10
    assert payload["mode"] == "two_pass"

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

    assert request_event["question"] == prompt
    assert response_event["request_id"] == request_event["request_id"]
    assert response_event["response_status"] == 200
    assert response_event["response"]["answer"]


def test_chat_pipeline_error_logs_structured_json(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: TwoPassEngine, question: str, history: list[dict[str, str]], request_id: str | None = None):
        del self, question, history, request_id
        raise RuntimeError("forced failure")

    monkeypatch.setattr(TwoPassEngine, "ask", boom)
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


def test_multi_turn_mayor_follow_ups(client: TestClient) -> None:
    history: list[dict[str, str]] = []

    # Turn 1: How long is the mayor's term?
    r1 = client.post("/api/chat", json={"message": "How long is the mayor's term?", "history": history})
    assert r1.status_code == 200
    p1 = r1.json()
    assert "2 year" in p1["answer"].lower()
    history.append({"role": "user", "content": "How long is the mayor's term?"})
    history.append({"role": "assistant", "content": p1["answer"]})

    # Turn 2: How is he or she elected?
    r2 = client.post("/api/chat", json={"message": "How is he or she elected?", "history": history})
    assert r2.status_code == 200
    p2 = r2.json()
    assert p2["citations"]
    history.append({"role": "user", "content": "How is he or she elected?"})
    history.append({"role": "assistant", "content": p2["answer"]})

    # Turn 3: What are his or her executive powers?
    r3 = client.post("/api/chat", json={"message": "What are his or her executive powers?", "history": history})
    assert r3.status_code == 200
    p3 = r3.json()
    assert p3["citations"]


def test_multi_turn_school_committee_follow_ups(client: TestClient) -> None:
    history: list[dict[str, str]] = []

    # Turn 1: How many members are on the school committee?
    r1 = client.post("/api/chat", json={"message": "How many members are on the school committee?", "history": history})
    assert r1.status_code == 200
    p1 = r1.json()
    assert p1["citations"]
    history.append({"role": "user", "content": "How many members are on the school committee?"})
    history.append({"role": "assistant", "content": p1["answer"]})

    # Turn 2: How is the chair chosen? (resolves → school committee chair)
    r2 = client.post("/api/chat", json={"message": "How is the chair chosen?", "history": history})
    assert r2.status_code == 200
    p2 = r2.json()
    assert p2["citations"]
    history.append({"role": "user", "content": "How is the chair chosen?"})
    history.append({"role": "assistant", "content": p2["answer"]})

    # Turn 3: What powers does it have? (resolves "it" → school committee)
    r3 = client.post("/api/chat", json={"message": "What powers does it have?", "history": history})
    assert r3.status_code == 200
    p3 = r3.json()
    assert p3["citations"]
