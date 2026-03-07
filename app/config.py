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
    model_name: str
    anthropic_api_key: str | None
    request_timeout_seconds: float
    max_history_messages: int
    max_output_tokens: int
    toc_search_limit: int
    observability_log_level: str


def load_settings() -> AppSettings:
    if load_dotenv is not None:
        load_dotenv(APP_ROOT / ".env", override=False)

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("MODEL_API_KEY")

    return AppSettings(
        non_zoning_markdown=Path(os.getenv("NON_ZONING_MARKDOWN", APP_ROOT / "somerville-law-non-zoning.md")),
        zoning_markdown=Path(os.getenv("ZONING_MARKDOWN", APP_ROOT / "somerville-zoning.md")),
        non_zoning_readable_html=Path(
            os.getenv("NON_ZONING_READABLE_HTML", APP_ROOT / "somerville-law-non-zoning.readable.html")
        ),
        zoning_readable_html=Path(
            os.getenv("ZONING_READABLE_HTML", APP_ROOT / "somerville-zoning.readable.html")
        ),
        model_name=os.getenv("MODEL_NAME", "claude-sonnet-4-6"),
        anthropic_api_key=anthropic_api_key,
        request_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
        max_history_messages=max(0, int(os.getenv("MAX_HISTORY_MESSAGES", "8"))),
        max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "4096")),
        toc_search_limit=int(os.getenv("TOC_SEARCH_LIMIT", "8")),
        observability_log_level=os.getenv("OBSERVABILITY_LOG_LEVEL", "INFO").strip().upper(),
    )
