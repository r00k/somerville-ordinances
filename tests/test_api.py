from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import SomervilleLawAgent
from app.api import AppRuntime, build_runtime, create_app
from app.config import AppSettings
from app.observability import LOGGER_NAME

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MODEL_API_KEY", "")

_TEST_MODEL = os.environ.get("TEST_MODEL", "claude-sonnet-4-6")


@pytest.fixture(scope="session")
def _runtime() -> AppRuntime:
    if not ANTHROPIC_API_KEY:
        pytest.skip("ANTHROPIC_API_KEY not set")
    root = Path(__file__).resolve().parent.parent
    settings = AppSettings(
        non_zoning_markdown=root / "somerville-law-non-zoning.md",
        zoning_markdown=root / "somerville-zoning.md",
        non_zoning_readable_html=root / "somerville-law-non-zoning.readable.html",
        zoning_readable_html=root / "somerville-zoning.readable.html",
        model_name=_TEST_MODEL,
        anthropic_api_key=ANTHROPIC_API_KEY,
        request_timeout_seconds=60.0,
        max_history_messages=8,
        max_output_tokens=1024,
        toc_search_limit=8,
        observability_log_level="INFO",
    )
    return build_runtime(settings)


@pytest.fixture(scope="session")
def client(_runtime: AppRuntime) -> TestClient:
    app = create_app(_runtime)
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
    assert payload["mode"] == "agent"

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
    async def boom(self, *, question, history, request_id=None):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(SomervilleLawAgent, "ask", boom)
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


async def _run_mayor_conversation(agent: SomervilleLawAgent) -> None:
    """Multi-turn mayor conversation — 3 turns, sequential within."""
    history: list[dict[str, str]] = []

    r1 = await agent.ask(question="How long is the mayor's term?", history=history)
    assert "2 year" in r1.answer.lower()
    history.append({"role": "user", "content": "How long is the mayor's term?"})
    history.append({"role": "assistant", "content": r1.answer})

    r2 = await agent.ask(question="How is he or she elected?", history=history)
    assert r2.citations
    history.append({"role": "user", "content": "How is he or she elected?"})
    history.append({"role": "assistant", "content": r2.answer})

    r3 = await agent.ask(question="What are his or her executive powers?", history=history)
    assert r3.citations


async def _run_school_committee_conversation(agent: SomervilleLawAgent) -> None:
    """Multi-turn school committee conversation — 3 turns, sequential within."""
    history: list[dict[str, str]] = []

    r1 = await agent.ask(question="How many members are on the school committee?", history=history)
    assert r1.citations
    history.append({"role": "user", "content": "How many members are on the school committee?"})
    history.append({"role": "assistant", "content": r1.answer})

    r2 = await agent.ask(question="How is the chair chosen?", history=history)
    assert r2.citations
    history.append({"role": "user", "content": "How is the chair chosen?"})
    history.append({"role": "assistant", "content": r2.answer})

    r3 = await agent.ask(question="What powers does it have?", history=history)
    assert r3.citations


def test_multi_turn_conversations(_runtime: AppRuntime) -> None:
    """Run mayor and school committee multi-turn conversations concurrently."""
    agent = _runtime.agent

    async def run_both() -> None:
        await asyncio.gather(
            _run_mayor_conversation(agent),
            _run_school_committee_conversation(agent),
        )

    asyncio.run(run_both())
