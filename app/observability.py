from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER_NAME = "somerville.observability"

_CONFIGURED = False


def configure_observability(level_name: str = "INFO") -> None:
    """Configure a dedicated logger that emits one structured JSON object per line."""

    global _CONFIGURED

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_coerce_log_level(level_name))

    if _CONFIGURED:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.propagate = False
    _CONFIGURED = True


def log_event(event: str, *, level: str = "info", **fields: Any) -> None:
    if not _CONFIGURED:
        configure_observability()

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event,
        **fields,
    }

    logger = logging.getLogger(LOGGER_NAME)
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(json.dumps(payload, ensure_ascii=True, default=_json_default, separators=(",", ":")))


def serialize_exception(exc: BaseException) -> dict[str, str]:
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
    }


def _coerce_log_level(level_name: str) -> int:
    value = getattr(logging, level_name.upper(), None)
    if isinstance(value, int):
        return value
    return logging.INFO


def _json_default(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return str(value)
