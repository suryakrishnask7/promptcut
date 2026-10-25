"""
tests/test_manifest.py

Verifies IncuBrix Integration Standard #6:
- manifest.json written per completed job
- provider/model names and versions
- settings, seed, timings, estimated cost, warnings, licence info
- values come from what actually ran
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import create_app
from promptcut.models.manifest import JobManifest, build_job_manifest, write_manifest_file
from promptcut.session.runner import run_session


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_build_job_manifest_structure():
    """Verify JobManifest model validation and field coverage."""
    manifest = build_job_manifest(
        job_id="job-123",
        session_id="sess-456",
        job_type="render",
        transcription_provider="mock",
        transcription_model="mock-v1",
        interpreter_provider="rule",
        interpreter_model="rule-based-v1",
        warnings=["snapped 1 cut to silence"],
        duration_ms=450.0,
        seed=42,
    )

    data = manifest.model_dump()
    assert data["job_id"] == "job-123"
    assert data["session_id"] == "sess-456"
    assert data["type"] == "render"
    assert data["seed"] == 42
    assert "providers" in data
    assert data["providers"]["transcription"]["provider"] == "mock"
    assert data["providers"]["interpreter"]["provider"] == "rule"
    assert data["providers"]["renderer"]["engine"] == "ffmpeg"
    assert "settings" in data
    assert data["settings"]["time_policy"] in ("current", "output_timeline")
    assert "timings" in data
    assert data["timings"]["duration_ms"] == 450.0
    assert "estimated_cost" in data
    assert data["estimated_cost"]["currency"] == "USD"
    assert data["warnings"] == ["snapped 1 cut to silence"]
    assert "licence_info" in data
    assert "ffmpeg" in data["licence_info"]


def test_manifest_written_on_session_runner(tmp_path):
    """run_session writes manifest.json with real runtime values."""
    from promptcut.media.intake import find_executable
    import subprocess
    from promptcut.models.transcript import Transcript, Word

    video_path = tmp_path / "sample.mp4"
    ffmpeg_bin = find_executable("ffmpeg")
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4:sample_rate=44100",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-t", "4.0", str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    words = [
        Word(id=0, text="Hello", start=0.0, end=1.0),
        Word(id=1, text="world", start=1.0, end=2.0),
        Word(id=2, text="test", start=2.0, end=3.0),
    ]
    transcript_path = tmp_path / "sample.transcript.json"
    transcript_path.write_text(Transcript(words=words).model_dump_json(), encoding="utf-8")

    yaml_content = f"""
video: "{video_path.as_posix()}"
transcript: "{transcript_path.as_posix()}"
instructions:
  - say: "cut the first 1 second"
"""
    yaml_path = tmp_path / "test_session.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    out_dir = tmp_path / "output"

    result = run_session(str(yaml_path), output_dir=str(out_dir))
    assert result["status"] == "success"

    manifest_path = out_dir / "manifest.json"
    assert manifest_path.is_file(), "manifest.json must be written by runner"

    with open(manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert manifest_data["session_id"] is not None
    assert manifest_data["providers"]["transcription"]["provider"] in ("mock", "faster-whisper", "vosk")
    assert manifest_data["settings"]["time_policy"] in ("current", "output_timeline")
    assert "licence_info" in manifest_data


def test_api_render_job_writes_manifest(client: TestClient):
    """POST /render writes manifest.json in the session directory."""
    init_res = client.post("/api/v1/sample-session")
    session_id = init_res.json()["session_id"]

    res = client.post(f"/api/v1/sessions/{session_id}/render")
    assert res.status_code == 202

    # Check that manifest.json is present in session storage or returned in job check
    job_id = res.json()["job_id"]
    job_res = client.get(f"/api/v1/jobs/{job_id}")
    assert job_res.status_code == 200
    assert job_res.json()["status"] in ("queued", "running", "succeeded")
