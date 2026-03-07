from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import SomervilleLawAgent
from .config import AppSettings, load_settings
from .observability import configure_observability, log_event, serialize_exception
from .toc import CorpusToc, build_corpus_toc


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)


class CitationResponse(BaseModel):
    quote: str
    source_heading: str
    reason: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    confidence: Literal["low", "medium", "high"]
    needs_clarification: bool
    clarification_question: Optional[str]
    selected_chapters: list[str]


@dataclass(frozen=True)
class AppRuntime:
    settings: AppSettings
    toc: CorpusToc
    agent: SomervilleLawAgent


def build_runtime(settings: AppSettings | None = None) -> AppRuntime:
    settings = settings or load_settings()

    non_zoning_text = settings.non_zoning_markdown.read_text(encoding="utf-8")
    zoning_text = settings.zoning_markdown.read_text(encoding="utf-8")
    toc = build_corpus_toc(non_zoning_text, zoning_text)

    if not settings.anthropic_api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY (or MODEL_API_KEY).")

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.request_timeout_seconds,
    )
    agent = SomervilleLawAgent(settings=settings, toc=toc, client=client)

    return AppRuntime(settings=settings, toc=toc, agent=agent)


@lru_cache(maxsize=1)
def get_runtime() -> AppRuntime:
    return build_runtime()


def create_app(runtime: AppRuntime | None = None) -> FastAPI:
    app = FastAPI(title="Somerville Law Assistant", version="0.1.0")
    active_runtime = runtime or get_runtime()
    configure_observability(active_runtime.settings.observability_log_level)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def read_root() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/documents/non-zoning")
    async def read_non_zoning_document() -> FileResponse:
        document_path = active_runtime.settings.non_zoning_readable_html
        if not document_path.exists():
            raise HTTPException(status_code=404, detail="Non-zoning readable HTML file not found.")
        return FileResponse(document_path)

    @app.get("/documents/zoning")
    async def read_zoning_document() -> FileResponse:
        document_path = active_runtime.settings.zoning_readable_html
        if not document_path.exists():
            raise HTTPException(status_code=404, detail="Zoning readable HTML file not found.")
        return FileResponse(document_path)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "provider": "anthropic",
            "model": active_runtime.settings.model_name,
            "chapters_loaded": len(active_runtime.toc.chapters),
            "mode": "agent",
        }

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
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

        max_history = active_runtime.settings.max_history_messages
        history_items = req.history[-max_history:] if max_history else []
        history = [{"role": item.role, "content": item.content.strip()} for item in history_items]

        log_event(
            "chat.request_received",
            request_id=request_id,
            question=message,
            history=history,
            history_count=len(history),
            model=active_runtime.settings.model_name,
        )

        try:
            result = await active_runtime.agent.ask(
                question=message, history=history, request_id=request_id
            )
        except Exception as exc:
            log_event(
                "chat.request_failed",
                level="error",
                request_id=request_id,
                question=message,
                history=history,
                response_status=500,
                error=serialize_exception(exc),
                response_body={"detail": f"Agent pipeline failed: {exc}"},
            )
            raise HTTPException(status_code=500, detail=f"Agent pipeline failed: {exc}") from exc

        citations = [
            CitationResponse(
                quote=item.quote,
                source_heading=item.source_heading,
                reason=item.reason,
            )
            for item in result.citations
        ]

        response = ChatResponse(
            answer=result.answer,
            citations=citations,
            confidence=result.confidence,
            needs_clarification=result.needs_clarification,
            clarification_question=result.clarification_question,
            selected_chapters=result.selected_chapters,
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
