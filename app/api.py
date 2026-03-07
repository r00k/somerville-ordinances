from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import AppSettings, load_settings
from .corpus import CorpusBundle, load_corpus_sections
from .observability import configure_observability, log_event, serialize_exception
from .provider import CorpusModel, OpenAICorpusModel
from .qa import AnswerEngine
from .types import CorpusName


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)


class CitationResponse(BaseModel):
    corpus: Literal["non_zoning", "zoning"]
    secid: str
    heading: str
    source_file: str
    quote: str
    reason: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    confidence: Literal["low", "medium", "high"]
    refused: bool
    needs_clarification: bool
    clarification_question: Optional[str]
    routed_corpus: Optional[Literal["non_zoning", "zoning"]]


@dataclass(frozen=True)
class AppRuntime:
    settings: AppSettings
    corpus_bundle: CorpusBundle
    models: dict[CorpusName, CorpusModel]
    engine: AnswerEngine


def build_runtime(
    settings: AppSettings | None = None,
    models: dict[CorpusName, CorpusModel] | None = None,
) -> AppRuntime:
    settings = settings or load_settings()
    bundle = load_corpus_sections(settings.non_zoning_markdown, settings.zoning_markdown)
    active_models = models or build_corpus_models(settings, bundle)
    engine = AnswerEngine(settings=settings, corpus_bundle=bundle, models=active_models)
    return AppRuntime(settings=settings, corpus_bundle=bundle, models=active_models, engine=engine)


def build_corpus_models(settings: AppSettings, bundle: CorpusBundle) -> dict[CorpusName, CorpusModel]:
    non_zoning_sections = [section for section in bundle.sections if section.corpus == "non_zoning"]
    zoning_sections = [section for section in bundle.sections if section.corpus == "zoning"]

    if not non_zoning_sections or not zoning_sections:
        raise RuntimeError("Both non-zoning and zoning corpora must contain sections.")

    return {
        "non_zoning": OpenAICorpusModel(
            corpus="non_zoning",
            model_name=settings.model_name,
            api_key=settings.openai_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            sections=non_zoning_sections,
        ),
        "zoning": OpenAICorpusModel(
            corpus="zoning",
            model_name=settings.model_name,
            api_key=settings.openai_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            sections=zoning_sections,
        ),
    }


@lru_cache(maxsize=1)
def get_runtime() -> AppRuntime:
    return build_runtime()


def create_app(runtime: AppRuntime | None = None) -> FastAPI:
    app = FastAPI(title="Somerville Law Assistant", version="0.1.0")
    active_runtime = runtime

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def runtime_ref() -> AppRuntime:
        nonlocal active_runtime
        if active_runtime is None:
            active_runtime = get_runtime()
        configure_observability(active_runtime.settings.observability_log_level)
        return active_runtime

    @app.get("/")
    async def read_root() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict[str, object]:
        runtime_obj = runtime_ref()
        return {
            "status": "ok",
            "model": runtime_obj.settings.model_name,
            "non_zoning_sections": sum(1 for section in runtime_obj.corpus_bundle.sections if section.corpus == "non_zoning"),
            "zoning_sections": sum(1 for section in runtime_obj.corpus_bundle.sections if section.corpus == "zoning"),
            "non_zoning_context_chars": getattr(runtime_obj.models["non_zoning"], "corpus_context_chars", 0),
            "zoning_context_chars": getattr(runtime_obj.models["zoning"], "corpus_context_chars", 0),
        }

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        runtime_obj = runtime_ref()
        request_id = str(uuid4())
        message = req.message.strip()
        if not message:
            log_event(
                "chat.validation_failed",
                level="warning",
                request_id=request_id,
                question=req.message,
                response_status=400,
                error={"type": "ValidationError", "message": "Message cannot be empty."},
            )
            raise HTTPException(status_code=400, detail="Message cannot be empty.")

        max_history = runtime_obj.settings.max_history_messages
        history_items = req.history[-max_history:] if max_history else []
        history = [{"role": item.role, "content": item.content.strip()} for item in history_items]

        log_event(
            "chat.request_received",
            request_id=request_id,
            question=message,
            history=history,
            history_count=len(history),
            model=runtime_obj.settings.model_name,
        )

        try:
            result = runtime_obj.engine.ask(question=message, history=history, request_id=request_id)
        except Exception as exc:  # pragma: no cover - surfaced in API response for manual testing
            log_event(
                "chat.request_failed",
                level="error",
                request_id=request_id,
                question=message,
                history=history,
                response_status=500,
                error=serialize_exception(exc),
                response_body={"detail": f"QA pipeline failed: {exc}"},
            )
            raise HTTPException(status_code=500, detail=f"QA pipeline failed: {exc}") from exc

        citations = [
            CitationResponse(
                corpus=item.corpus,
                secid=item.secid,
                heading=item.heading,
                source_file=item.source_file,
                quote=item.quote,
                reason=item.reason,
            )
            for item in result.citations
        ]

        response = ChatResponse(
            answer=result.answer,
            citations=citations,
            confidence=result.confidence,
            refused=result.refused,
            needs_clarification=result.needs_clarification,
            clarification_question=result.clarification_question,
            routed_corpus=result.routed_corpus,
        )

        log_event(
            "chat.response_emitted",
            request_id=request_id,
            question=message,
            response_status=200,
            response=response.model_dump(),
        )

        return response

    return app


app = create_app()
