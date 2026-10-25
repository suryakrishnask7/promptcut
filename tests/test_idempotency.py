"""
tests/test_idempotency.py

Verifies IncuBrix Integration Standard #4 (Safe Retries):
- Idempotency-Key header on POST endpoints (/sessions, /instructions, /render)
- Same key + same body returns stored response and does not re-execute
- Same key + different body returns 409 IDEMPOTENCY_KEY_REUSED
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from promptcut.api.app import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_idempotent_instruction_replay(client: TestClient):
    """Same Idempotency-Key + same body returns identical cached response without re-executing."""
    init_res = client.post("/api/v1/sample-session")
    session_id = init_res.json()["session_id"]

    idempotency_key = f"key-{uuid.uuid4().hex}"
    payload = {"text": "cut the first 2 seconds"}

    # First execution
    res1 = client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "applied"
    dur1 = data1["duration_s"]

    # Replay with same key and same body
    res2 = client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "applied"
    assert data2["duration_s"] == dur1
    # Must have X-Idempotent-Replay header or match response
    assert res2.headers.get("X-Idempotent-Replay") == "true"


def test_idempotency_key_reused_with_different_body_returns_409(client: TestClient):
    """Same Idempotency-Key + different body returns 409 IDEMPOTENCY_KEY_REUSED."""
    init_res = client.post("/api/v1/sample-session")
    session_id = init_res.json()["session_id"]

    idempotency_key = f"key-{uuid.uuid4().hex}"

    # First request
    res1 = client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "undo"},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res1.status_code == 200

    # Second request with SAME key but DIFFERENT body
    res2 = client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "redo"},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res2.status_code == 409
    err = res2.json()["error"]
    assert err["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert err["retryable"] is False


def test_idempotent_render_replay(client: TestClient):
    """Same Idempotency-Key on render endpoint returns cached 202 without re-dispatching."""
    init_res = client.post("/api/v1/sample-session")
    session_id = init_res.json()["session_id"]

    idempotency_key = f"render-key-{uuid.uuid4().hex}"

    res1 = client.post(
        f"/api/v1/sessions/{session_id}/render",
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res1.status_code == 202
    job_id_1 = res1.json()["job_id"]

    res2 = client.post(
        f"/api/v1/sessions/{session_id}/render",
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res2.status_code == 202
    assert res2.headers.get("X-Idempotent-Replay") == "true"
    assert res2.json()["job_id"] == job_id_1


def test_idempotent_session_creation(client: TestClient, tmp_path):
    """Same Idempotency-Key on session creation returns cached response; different body returns 409."""
    video_path = tmp_path / "test.mp4"
    video_path.write_bytes(b"dummy video content")

    idempotency_key = f"session-key-{uuid.uuid4().hex}"

    # First creation with video_path
    res1 = client.post(
        "/api/v1/sessions",
        json={"video_path": str(video_path)},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res1.status_code == 202
    sess_id_1 = res1.json()["session_id"]

    # Replay same key + same body
    res2 = client.post(
        "/api/v1/sessions",
        json={"video_path": str(video_path)},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res2.status_code == 202
    assert res2.headers.get("X-Idempotent-Replay") == "true"
    assert res2.json()["session_id"] == sess_id_1

    # Conflict with same key + different body
    other_path = tmp_path / "other.mp4"
    other_path.write_bytes(b"other content")
    res3 = client.post(
        "/api/v1/sessions",
        json={"video_path": str(other_path)},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert res3.status_code == 409
    assert res3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

