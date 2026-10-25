"""
src/promptcut/api/logging_config.py

IncuBrix Integration Standard #8 (Logs and Health):
- Structured JSON logs with timestamp, level, job_id, and X-Correlation-Id
- Secret redaction (never log GROQ_API_KEY, Bearer tokens, or passwords)
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any

correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")
job_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="")


def get_correlation_id() -> str:
    return correlation_id_ctx.get()


def set_correlation_id(val: str) -> None:
    correlation_id_ctx.set(val)


def get_job_id() -> str:
    return job_id_ctx.get()


def set_job_id(val: str) -> None:
    job_id_ctx.set(val)


class SecretRedactingFilter(logging.Filter):
    """Filter that masks sensitive tokens (e.g. GROQ_API_KEY, gsk_*, Bearer tokens)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.msg, str):
            record.msg = str(record.msg)

        # 1. Redact exact GROQ_API_KEY if present in environment
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_key and len(groq_key) > 5 and groq_key in record.msg:
            record.msg = record.msg.replace(groq_key, "[REDACTED]")

        # 2. Redact common API key patterns (gsk_*, Bearer ...)
        record.msg = re.sub(r"gsk_[a-zA-Z0-9_-]{10,}", "[REDACTED]", record.msg)
        record.msg = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{10,}", "Bearer [REDACTED]", record.msg)

        if record.args:
            redacted_args = []
            for arg in record.args:
                arg_str = str(arg)
                if groq_key and len(groq_key) > 5 and groq_key in arg_str:
                    arg_str = arg_str.replace(groq_key, "[REDACTED]")
                arg_str = re.sub(r"gsk_[a-zA-Z0-9_-]{10,}", "[REDACTED]", arg_str)
                arg_str = re.sub(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{10,}", "Bearer [REDACTED]", arg_str)
                redacted_args.append(arg_str)
            record.args = tuple(redacted_args)

        return True


class StructuredJsonFormatter(logging.Formatter):
    """Outputs single-line structured JSON logs with standard metadata."""

    def format(self, record: logging.LogRecord) -> str:
        # Determine correlation_id and job_id from record or contextvar
        corr_id = getattr(record, "correlation_id", None) or get_correlation_id() or ""
        j_id = getattr(record, "job_id", None) or get_job_id() or ""

        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": corr_id,
            "job_id": j_id,
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def configure_structured_logging(level: int = logging.INFO) -> None:
    """Configure promptcut structured JSON logger with secret redaction."""
    logger = logging.getLogger("promptcut")
    logger.setLevel(level)

    # Avoid duplicate handlers if called multiple times
    if not any(isinstance(h.formatter, StructuredJsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJsonFormatter())
        handler.addFilter(SecretRedactingFilter())
        logger.addHandler(handler)
        logger.propagate = False
