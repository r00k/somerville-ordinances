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
    model_name: str
    openai_api_key: str
    request_timeout_seconds: float
    max_history_messages: int
    observability_log_level: str


def load_settings() -> AppSettings:
    if load_dotenv is not None:
        load_dotenv(APP_ROOT / ".env", override=False)

    return AppSettings(
        non_zoning_markdown=Path(os.getenv("NON_ZONING_MARKDOWN", APP_ROOT / "somerville-law-non-zoning.md")),
        zoning_markdown=Path(os.getenv("ZONING_MARKDOWN", APP_ROOT / "somerville-zoning.md")),
        model_name=os.getenv("MODEL_NAME", "gpt-5.4").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        request_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
        max_history_messages=max(0, int(os.getenv("MAX_HISTORY_MESSAGES", "8"))),
        observability_log_level=os.getenv("OBSERVABILITY_LOG_LEVEL", "INFO").strip().upper(),
    )
