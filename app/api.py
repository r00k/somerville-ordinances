from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import AppSettings, load_settings
from .corpus import CorpusBundle, load_corpus_sections
from .provider import ModelProvider, build_provider
from .qa import AnswerEngine
from .retrieval import SectionIndex


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
    score: float


class ChatResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    confidence: Literal["low", "medium", "high"]
    refused: bool
    needs_clarification: bool
    clarification_question: Optional[str]
    used_long_context_verification: bool
    requested_corpora: list[str]


@dataclass(frozen=True)
class AppRuntime:
    settings: AppSettings
    corpus_bundle: CorpusBundle
    index: SectionIndex
    provider: ModelProvider
    engine: AnswerEngine


def build_runtime(settings: AppSettings | None = None) -> AppRuntime:
    settings = settings or load_settings()
    bundle = load_corpus_sections(settings.non_zoning_markdown, settings.zoning_markdown)
    index = SectionIndex(bundle.sections)
    provider = build_provider(settings)
    engine = AnswerEngine(settings=settings, corpus_bundle=bundle, index=index, provider=provider)
    return AppRuntime(settings=settings, corpus_bundle=bundle, index=index, provider=provider, engine=engine)


@lru_cache(maxsize=1)
def get_runtime() -> AppRuntime:
    return build_runtime()


def create_app(runtime: AppRuntime | None = None) -> FastAPI:
    app = FastAPI(title="Somerville Law Assistant", version="0.1.0")
    active_runtime = runtime or get_runtime()

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def read_root() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "provider": active_runtime.provider.name,
            "model": active_runtime.settings.model_name,
            "sections_loaded": len(active_runtime.corpus_bundle.sections),
            "retrieval_top_k": active_runtime.settings.retrieval_top_k,
            "long_context_verification": active_runtime.settings.enable_long_context_verification,
        }

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty.")

        max_history = active_runtime.settings.max_history_messages
        history_items = req.history[-max_history:] if max_history else []
        history = [{"role": item.role, "content": item.content.strip()} for item in history_items]

        try:
            result = active_runtime.engine.ask(question=message, history=history)
        except Exception as exc:  # pragma: no cover - surfaced in API response for manual testing
            raise HTTPException(status_code=500, detail=f"QA pipeline failed: {exc}") from exc

        citations = [
            CitationResponse(
                corpus=item.corpus,
                secid=item.secid,
                heading=item.heading,
                source_file=item.source_file,
                quote=item.quote,
                reason=item.reason,
                score=item.score,
            )
            for item in result.citations
        ]

        return ChatResponse(
            answer=result.answer,
            citations=citations,
            confidence=result.confidence,
            refused=result.refused,
            needs_clarification=result.needs_clarification,
            clarification_question=result.clarification_question,
            used_long_context_verification=result.used_long_context_verification,
            requested_corpora=sorted(result.retrieval_trace.requested_corpora),
        )

    return app


app = create_app()
