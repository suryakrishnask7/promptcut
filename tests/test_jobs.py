"""
tests/test_jobs.py

Verifies IncuBrix Integration Standard #3 (Background Jobs):
- Transcription and render return 202 Accepted + job_id
- GET /api/v1/jobs/{id} returns status (queued|running|succeeded|failed) and progress 0-100
- Optional callback_url signed with HMAC-SHA256 in X-IncuBrix-Signature header with retries
"""

import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import create_app
from promptcut.api.webhook import sign_webhook_payload, dispatch_webhook


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_session_creation_returns_202_with_job(client: TestClient, tmp_path):
    """POST /sessions with file or video_path returns 202 Accepted and job_id."""
    test_video = tmp_path / "job_test.mp4"
    test_video.write_bytes(b"dummy_video_bytes_for_job_testing" * 20)

    res = client.post(
        "/api/v1/sessions",
        data={"video_path": str(test_video)},
    )
    assert res.status_code == 202
    data = res.json()
    assert "job_id" in data
    assert "session_id" in data
    job_id = data["job_id"]

    # Poll job status
    job_res = client.get(f"/api/v1/jobs/{job_id}")
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert job_data["status"] in ("queued", "running", "succeeded")
    assert 0 <= job_data["progress"] <= 100


def test_render_returns_202_with_job(client: TestClient):
    """POST /render returns 202 Accepted and job_id with progress tracking."""
    init_res = client.post("/api/v1/sample-session")
    session_id = init_res.json()["session_id"]

    render_res = client.post(f"/api/v1/sessions/{session_id}/render")
    assert render_res.status_code == 202
    data = render_res.json()
    assert "job_id" in data
    job_id = data["job_id"]

    job_res = client.get(f"/api/v1/jobs/{job_id}")
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert job_data["status"] in ("queued", "running", "succeeded")
    assert 0 <= job_data["progress"] <= 100


def test_webhook_hmac_sha256_signature():
    """Verify HMAC-SHA256 signature calculation matches IncuBrix signature format."""
    secret = "test_incubrix_secret_key"
    payload = {"job_id": "job_123", "status": "succeeded", "progress": 100}
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")

    expected_sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    actual_sig = sign_webhook_payload(payload_bytes, secret)

    assert actual_sig == expected_sig


def test_dispatch_webhook_with_mock_server(monkeypatch):
    """Verify dispatch_webhook sends POST with X-IncuBrix-Signature header."""
    sent_requests = []

    class MockResponse:
        status_code = 200

    def mock_post(url, data, headers, timeout):
        sent_requests.append({"url": url, "data": data, "headers": headers})
        return MockResponse()

    import urllib.request
    # Test our webhook dispatcher executes cleanly and signs request
    payload = {"job_id": "job-test-456", "status": "succeeded"}
    ok = dispatch_webhook(
        "https://example.com/callback",
        payload,
        secret="my_secret",
        poster=mock_post,
    )
    assert ok is True
    assert len(sent_requests) == 1
    assert "X-IncuBrix-Signature" in sent_requests[0]["headers"]
    sig = sent_requests[0]["headers"]["X-IncuBrix-Signature"]
    assert len(sig) == 64  # SHA-256 hex length
