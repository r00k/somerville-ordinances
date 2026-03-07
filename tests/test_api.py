from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import AppRuntime, create_app
from app.config import AppSettings
from app.corpus import load_corpus_sections
from app.observability import LOGGER_NAME
from app.qa import AnswerEngine
from app.types import CorpusName, CorpusSection


class FakeCorpusModel:
    def __init__(self, *, corpus: CorpusName, section: CorpusSection):
        self.corpus = corpus
        self.model_name = "gpt-5.4"
        self.section_count = 1
        self.corpus_context_chars = len(section.text)
        self._section = section
        self.calls: list[str] = []

    def generate(self, *, question: str, history: list[dict[str, str]]) -> str:
        del history
        self.calls.append(question)
        quote = " ".join(self._section.text.split())[:220]
        q = question.lower()

        if self.corpus == "non_zoning" and "city council" in q:
            payload = {
                "answer_markdown": "Somerville's city council has **11 members**.",
                "citations": [
                    {
                        "corpus": "non_zoning",
                        "secid": self._section.secid,
                        "quote": quote,
                        "reason": "Defines the city council composition",
                    }
                ],
                "confidence": "high",
                "insufficient_context": False,
                "clarification_question": None,
            }
            return json.dumps(payload)

        if self.corpus == "zoning" and "affordable" in q and "20" in q and "2" in q:
            payload = {
                "answer_markdown": (
                    "For inclusionary zoning, a project with **2 units requires 0 affordable units** and "
                    "a project with **20 units requires 4 affordable units**."
                ),
                "citations": [
                    {
                        "corpus": "zoning",
                        "secid": self._section.secid,
                        "quote": quote,
                        "reason": "Inclusionary zoning requirement",
                    }
                ],
                "confidence": "medium",
                "insufficient_context": False,
                "clarification_question": None,
            }
            return json.dumps(payload)

        if self.corpus == "zoning" and "demolish" in q:
            payload = {
                "answer_markdown": "No. You cannot demolish without required City permission.",
                "citations": [
                    {
                        "corpus": "zoning",
                        "secid": self._section.secid,
                        "quote": quote,
                        "reason": "Demolition permission requirement",
                    }
                ],
                "confidence": "medium",
                "insufficient_context": False,
                "clarification_question": None,
            }
            return json.dumps(payload)

        payload = {
            "answer_markdown": "I do not have enough grounded context.",
            "citations": [],
            "confidence": "low",
            "insufficient_context": True,
            "clarification_question": "Please narrow the question.",
        }
        return json.dumps(payload)


@pytest.fixture()
def test_runtime() -> tuple[TestClient, dict[CorpusName, FakeCorpusModel]]:
    root = Path(__file__).resolve().parent.parent
    settings = AppSettings(
        non_zoning_markdown=root / "somerville-law-non-zoning.md",
        zoning_markdown=root / "somerville-zoning.md",
        model_name="gpt-5.4",
        openai_api_key="test-key",
        request_timeout_seconds=30.0,
        max_history_messages=8,
        observability_log_level="INFO",
    )
    bundle = load_corpus_sections(settings.non_zoning_markdown, settings.zoning_markdown)

    non_zoning_section = next(section for section in bundle.sections if section.corpus == "non_zoning")
    zoning_section = next(section for section in bundle.sections if section.corpus == "zoning")

    models: dict[CorpusName, FakeCorpusModel] = {
        "non_zoning": FakeCorpusModel(corpus="non_zoning", section=non_zoning_section),
        "zoning": FakeCorpusModel(corpus="zoning", section=zoning_section),
    }
    engine = AnswerEngine(settings=settings, corpus_bundle=bundle, models=models)
    runtime = AppRuntime(settings=settings, corpus_bundle=bundle, models=models, engine=engine)

    app = create_app(runtime)
    return TestClient(app), models


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


def test_health_endpoint(test_runtime: tuple[TestClient, dict[CorpusName, FakeCorpusModel]]) -> None:
    client, _models = test_runtime
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["model"] == "gpt-5.4"
    assert payload["non_zoning_sections"] > 2000
    assert payload["zoning_sections"] > 800


@pytest.mark.parametrize(
    "prompt,expected_corpus,patterns,expected_confidence",
    [
        (
            "How many people sit on the Somerville city council?",
            "non_zoning",
            [r"\b11\b"],
            "high",
        ),
        (
            "What percentage of units must be affordable due to inclusionary zoning on projects with 2 units and 20 units?",
            "zoning",
            [r"\b2\s*units?\b[^\n]{0,120}\b0\b", r"\b20\s*units?\b[^\n]{0,120}\b4\b"],
            "medium",
        ),
        (
            "Can you demolish a 100 year-old building in Somerville without permission?",
            "zoning",
            [r"\bno\b"],
            "medium",
        ),
    ],
)
def test_questions_route_to_single_corpus_model(
    test_runtime: tuple[TestClient, dict[CorpusName, FakeCorpusModel]],
    prompt: str,
    expected_corpus: CorpusName,
    patterns: list[str],
    expected_confidence: str,
) -> None:
    client, models = test_runtime
    response = client.post("/api/chat", json={"message": prompt, "history": []})
    assert response.status_code == 200
    payload = response.json()

    assert payload["refused"] is False
    assert payload["routed_corpus"] == expected_corpus
    assert payload["confidence"] == expected_confidence
    assert len(payload["citations"]) >= 1

    answer = payload["answer"]
    for pattern in patterns:
        assert re.search(pattern, answer, flags=re.IGNORECASE), answer

    for corpus, model in models.items():
        if corpus == expected_corpus:
            assert len(model.calls) == 1
        else:
            assert len(model.calls) == 0


def test_grounded_partial_answer_is_not_refused(
    test_runtime: tuple[TestClient, dict[CorpusName, FakeCorpusModel]],
) -> None:
    client, models = test_runtime
    zoning_model = models["zoning"]
    quote = " ".join(zoning_model._section.text.split())[:220]
    payload = {
        "answer_markdown": "No. Demolition requires authorization under the ordinance.",
        "citations": [
            {
                "corpus": "zoning",
                "secid": zoning_model._section.secid,
                "quote": quote,
                "reason": "Grounded demolition requirement",
            }
        ],
        "confidence": "medium",
        "insufficient_context": True,
        "clarification_question": None,
    }

    def grounded_partial(*, question: str, history: list[dict[str, str]]) -> str:
        del question, history
        return json.dumps(payload)

    zoning_model.generate = grounded_partial  # type: ignore[method-assign]

    response = client.post(
        "/api/chat",
        json={"message": "Can you demolish this building under zoning rules without permission?", "history": []},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["refused"] is False
    assert body["routed_corpus"] == "zoning"
    assert body["confidence"] == "low"
    assert body["needs_clarification"] is True
    assert len(body["citations"]) == 1

    assert models["non_zoning"].calls == []


def test_ambiguous_question_still_routes_and_answers(
    test_runtime: tuple[TestClient, dict[CorpusName, FakeCorpusModel]],
) -> None:
    client, models = test_runtime
    response = client.post(
        "/api/chat",
        json={"message": "What permits are required in Somerville?", "history": []},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["refused"] is False
    assert payload["routed_corpus"] is not None

    routed = payload["routed_corpus"]
    assert len(models[routed].calls) == 1


def test_chat_logs_routing_and_response_as_json(
    test_runtime: tuple[TestClient, dict[CorpusName, FakeCorpusModel]],
) -> None:
    client, _models = test_runtime
    prompt = "How many people sit on the Somerville city council?"

    logger, handler, messages = _capture_observability_messages()
    try:
        response = client.post("/api/chat", json={"message": prompt, "history": []})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200

    events = _parse_structured_events("\n".join(messages))
    request_event = next(event for event in events if event["event"] == "chat.request_received")
    routing_event = next(event for event in events if event["event"] == "qa.routing_decision")
    response_event = next(event for event in events if event["event"] == "chat.response_emitted")

    assert request_event["question"] == prompt
    assert routing_event["request_id"] == request_event["request_id"]
    assert routing_event["routed_corpus"] == "non_zoning"
    assert response_event["request_id"] == request_event["request_id"]
    assert response_event["response_status"] == 200


def test_chat_pipeline_error_logs_structured_json(
    test_runtime: tuple[TestClient, dict[CorpusName, FakeCorpusModel]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _models = test_runtime

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
