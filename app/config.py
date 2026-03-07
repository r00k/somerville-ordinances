from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency guard
    load_dotenv = None


APP_ROOT = Path(__file__).resolve().parent.parent



@dataclass(frozen=True)
class AppSettings:
    non_zoning_markdown: Path
    zoning_markdown: Path
    non_zoning_readable_html: Path
    zoning_readable_html: Path
    model_provider: str
    model_name: str
    model_api_key: str | None
    model_base_url: str | None
    request_timeout_seconds: float
    max_history_messages: int
    observability_log_level: str


def load_settings() -> AppSettings:
    if load_dotenv is not None:
        load_dotenv(APP_ROOT / ".env", override=False)

    model_provider = os.getenv("MODEL_PROVIDER", "openai").strip().lower()
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
        non_zoning_readable_html=Path(
            os.getenv("NON_ZONING_READABLE_HTML", APP_ROOT / "somerville-law-non-zoning.readable.html")
        ),
        zoning_readable_html=Path(
            os.getenv("ZONING_READABLE_HTML", APP_ROOT / "somerville-zoning.readable.html")
        ),
        model_provider=model_provider,
        model_name=model_name,
        model_api_key=model_api_key,
        model_base_url=os.getenv("MODEL_BASE_URL"),
        request_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
        max_history_messages=max(0, int(os.getenv("MAX_HISTORY_MESSAGES", "8"))),
        observability_log_level=os.getenv("OBSERVABILITY_LOG_LEVEL", "INFO").strip().upper(),
    )
