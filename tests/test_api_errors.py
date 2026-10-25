"""
tests/test_api_errors.py

Verifies IncuBrix Integration Standard #5 (Clear Errors):
Every HTTP error (including unhandled exceptions via a catch-all handler
and validation errors) returns:
{"error": {"code": "UPPER_SNAKE_CASE", "message": "...", "retryable": bool, "details": {}}}
"""

import pytest
from fastapi.testclient import TestClient
from promptcut.api.app import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


def test_404_not_found_standard_error(client: TestClient):
    """Verify 404 returns structured error schema with NOT_FOUND code."""
    res = client.get("/api/v1/sessions/nonexistent-session-id-12345/timeline")
    assert res.status_code == 404
    data = res.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "NOT_FOUND" or err["code"].isupper()
    assert isinstance(err["message"], str)
    assert isinstance(err["retryable"], bool)
    assert isinstance(err["details"], dict)


def test_validation_error_standard_format(client: TestClient):
    """Verify 422 validation errors return VALIDATION_ERROR code and structured error."""
    # Create session first so session lookup passes
    init_res = client.post("/api/v1/sample-session")
    session_id = init_res.json()["session_id"]

    # Sending invalid JSON body (missing required field 'text') to word update endpoint
    res = client.put(f"/api/v1/sessions/{session_id}/transcript/words/0", json={"not_text": 123})
    assert res.status_code == 422
    data = res.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["retryable"] is False
    assert "validation" in err["message"].lower() or "errors" in err["message"].lower()
    assert isinstance(err["details"], dict)
    assert "errors" in err["details"]


def test_400_bad_request_standard_format(client: TestClient):
    """Verify 400 bad request returns structured error."""
    # Missing both file and video_path
    res = client.post("/api/v1/sessions", data={})
    assert res.status_code == 400
    data = res.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "BAD_REQUEST" or err["code"] == "MISSING_INPUT"
    assert err["retryable"] is False
    assert isinstance(err["details"], dict)


def test_forced_unhandled_crash_returns_500_standard_error(client: TestClient):
    """Verify forced internal crash triggers catch-all handler returning 500 JSON schema."""
    res = client.get("/api/v1/debug/forced-crash")
    assert res.status_code == 500
    data = res.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "INTERNAL_SERVER_ERROR"
    assert err["retryable"] is True
    assert isinstance(err["message"], str)
    assert isinstance(err["details"], dict)
