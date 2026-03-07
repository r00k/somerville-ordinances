from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency guard
    load_dotenv = None


APP_ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppSettings:
    non_zoning_markdown: Path
    zoning_markdown: Path
    model_provider: str
    model_name: str
    model_api_key: str | None
    model_base_url: str | None
    request_timeout_seconds: float
    retrieval_top_k: int
    retrieval_excerpt_chars: int
    retrieval_min_score: float
    max_history_messages: int
    enable_long_context_verification: bool
    long_context_top_k: int
    long_context_trigger_min_confidence: str
    observability_log_level: str


def load_settings() -> AppSettings:
    if load_dotenv is not None:
        load_dotenv(APP_ROOT / ".env", override=False)

    model_provider = os.getenv("MODEL_PROVIDER", "mock").strip().lower()
    model_name = os.getenv("MODEL_NAME", "gpt-5.4")

    generic_key = os.getenv("MODEL_API_KEY")
    provider_key = None
    if model_provider == "openai":
        provider_key = os.getenv("OPENAI_API_KEY")
    elif model_provider == "anthropic":
        provider_key = os.getenv("ANTHROPIC_API_KEY")

    model_api_key = generic_key or provider_key

    return AppSettings(
        non_zoning_markdown=Path(os.getenv("NON_ZONING_MARKDOWN", APP_ROOT / "somerville-law-non-zoning.md")),
        zoning_markdown=Path(os.getenv("ZONING_MARKDOWN", APP_ROOT / "somerville-zoning.md")),
        model_provider=model_provider,
        model_name=model_name,
        model_api_key=model_api_key,
        model_base_url=os.getenv("MODEL_BASE_URL"),
        request_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
        retrieval_top_k=max(1, int(os.getenv("RETRIEVAL_TOP_K", "10"))),
        retrieval_excerpt_chars=max(400, int(os.getenv("RETRIEVAL_EXCERPT_CHARS", "1800"))),
        retrieval_min_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "0.05")),
        max_history_messages=max(0, int(os.getenv("MAX_HISTORY_MESSAGES", "8"))),
        enable_long_context_verification=_env_bool("ENABLE_LONG_CONTEXT_VERIFICATION", False),
        long_context_top_k=max(6, int(os.getenv("LONG_CONTEXT_TOP_K", "24"))),
        long_context_trigger_min_confidence=os.getenv("LONG_CONTEXT_TRIGGER_MIN_CONFIDENCE", "medium").strip().lower(),
        observability_log_level=os.getenv("OBSERVABILITY_LOG_LEVEL", "INFO").strip().upper(),
    )
