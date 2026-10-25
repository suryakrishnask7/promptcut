"""
tests/test_health_and_logging.py

Verifies IncuBrix Integration Standard #8 (Logs and Health):
- GET /health (liveness)
- GET /ready (readiness: checks storage, ffmpeg, interpreter)
- Structured JSON logs with timestamp, level, job_id, X-Correlation-Id
- X-Correlation-Id accepted or generated, echoed in response
- Secrets (e.g. GROQ_API_KEY) NEVER appear in log output
"""

import io
import json
import logging
import os
import uuid
import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import create_app
from promptcut.api.logging_config import SecretRedactingFilter, StructuredJsonFormatter, get_correlation_id


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_get_health_liveness(client: TestClient):
    """GET /health must return 200 with status ok."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "ok"


def test_get_ready_readiness(client: TestClient):
    """GET /ready must return 200 and check storage, ffmpeg, and interpreter."""
    res = client.get("/ready")
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "ready"
    checks = data.get("checks", {})
    assert "storage" in checks
    assert "ffmpeg" in checks
    assert "interpreter" in checks


def test_correlation_id_generated_and_echoed(client: TestClient):
    """If client sends no X-Correlation-Id, server generates one and echoes in header."""
    res = client.get("/health")
    assert res.status_code == 200
    corr_id = res.headers.get("X-Correlation-Id")
    assert corr_id is not None
    assert len(corr_id) >= 8


def test_correlation_id_accepted_and_echoed(client: TestClient):
    """If client sends X-Correlation-Id, server preserves it and echoes it in header."""
    custom_id = f"test-trace-{uuid.uuid4().hex[:8]}"
    res = client.get("/health", headers={"X-Correlation-Id": custom_id})
    assert res.status_code == 200
    assert res.headers.get("X-Correlation-Id") == custom_id


def test_structured_json_formatter_fields():
    """Verify JSON log formatter outputs timestamp, level, message, job_id, correlation_id."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="promptcut.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=50,
        msg="Processing video intake",
        args=(),
        exc_info=None,
    )
    record.correlation_id = "corr-12345"
    record.job_id = "job-67890"

    formatted = formatter.format(record)
    log_json = json.loads(formatted)
    assert log_json["level"] == "INFO"
    assert log_json["message"] == "Processing video intake"
    assert log_json["correlation_id"] == "corr-12345"
    assert log_json["job_id"] == "job-67890"
    assert "timestamp" in log_json


def test_never_log_secrets_groq_api_key_redaction(monkeypatch):
    """Verify that GROQ_API_KEY is scrubbed/redacted from log output."""
    secret_key = "gsk_super_secret_groq_api_token_1234567890abcdef"
    monkeypatch.setenv("GROQ_API_KEY", secret_key)

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(StructuredJsonFormatter())
    handler.addFilter(SecretRedactingFilter())

    test_logger = logging.getLogger("promptcut.security_test")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)

    test_logger.info(f"Connecting to Groq with key {secret_key} and authorization bearer {secret_key}")

    output = log_stream.getvalue()
    # The actual secret key must NEVER appear in log output
    assert secret_key not in output
    assert "[REDACTED]" in output
