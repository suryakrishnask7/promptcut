"""
tests/test_interpreted_by_and_modes.py

Verifies:
1. interpreted_by ("rule" | "llm" | "cache") on:
   - every instruction response
   - every operation log entry in OperationLog and GET /api/v1/sessions/{id}/log
   - results.json step entries and operation entries
2. INTERPRETER=auto|llm_only|rule_only environment flag:
   - llm_only: RuleInterpreter is bypassed entirely (no fallback even on error / no key)
   - rule_only: LLM is bypassed entirely; only RuleInterpreter allowlist is used
   - auto: RuleInterpreter explicit allowlist is fast-pathed, everything else goes to LLM
3. RuleInterpreter explicit allowlist restriction:
   - allowed: undo, redo, render, and explicit time ranges like "cut 0:30-0:40"
   - all other commands rejected with indication that LLM is required
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import create_app
from promptcut.config import get_interpreter_mode
from promptcut.interpreter import (
    GroqInterpreter,
    IntentTarget,
    InterpretationResult,
    InterpreterContext,
    RuleInterpreter,
)
from promptcut.media.intake import find_executable
from promptcut.models.operations import CutRange
from promptcut.models.operation_log import OperationLog
from promptcut.models.transcript import Transcript, Word
from promptcut.session.runner import run_session


@pytest.fixture(scope="module")
def api_client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_rule_interpreter_explicit_allowlist_and_rejections():
    """Verify that RuleInterpreter ONLY handles allowlisted commands and rejects all others."""
    rule = RuleInterpreter()
    ctx = InterpreterContext(duration=60.0)

    # 1. Allowlisted: undo
    res_undo = rule.interpret("undo", ctx)
    assert res_undo.status == "undo"
    assert res_undo.interpreted_by == "rule"

    res_undo_last = rule.interpret("undo last", ctx)
    assert res_undo_last.status == "undo"
    assert res_undo_last.interpreted_by == "rule"

    # 2. Allowlisted: redo
    res_redo = rule.interpret("redo", ctx)
    assert res_redo.status == "redo"
    assert res_redo.interpreted_by == "rule"

    # 3. Allowlisted: render
    res_render = rule.interpret("render", ctx)
    assert res_render.status == "render"
    assert res_render.interpreted_by == "rule"

    res_export = rule.interpret("export", ctx)
    assert res_export.status == "render"
    assert res_export.interpreted_by == "rule"

    # 4. Allowlisted: explicit time ranges
    # 4a. Hyphenated: "cut 0:30-0:40"
    res_hyphen = rule.interpret("cut 0:30-0:40", ctx)
    assert res_hyphen.status == "applied"
    assert res_hyphen.interpreted_by == "rule"
    assert len(res_hyphen.targets) == 1
    assert res_hyphen.targets[0].action == "cut"
    assert res_hyphen.targets[0].start_time == 30.0
    assert res_hyphen.targets[0].end_time == 40.0

    # 4b. "cut from 0:10 to 0:20"
    res_range = rule.interpret("cut from 0:10 to 0:20", ctx)
    assert res_range.status == "applied"
    assert res_range.interpreted_by == "rule"
    assert res_range.targets[0].start_time == 10.0
    assert res_range.targets[0].end_time == 20.0

    # 4c. "cut first 2s"
    res_first = rule.interpret("cut first 2s", ctx)
    assert res_first.status == "applied"
    assert res_first.interpreted_by == "rule"
    assert res_first.targets[0].start_time == 0.0
    assert res_first.targets[0].end_time == 2.0

    # 4d. "speed up from 1:00 to 1:30 by 2x"
    res_speed = rule.interpret("speed up from 1:00 to 1:30 by 2x", ctx)
    assert res_speed.status == "applied"
    assert res_speed.interpreted_by == "rule"
    assert res_speed.targets[0].action == "speed"
    assert res_speed.targets[0].start_time == 60.0
    assert res_speed.targets[0].end_time == 90.0
    assert res_speed.targets[0].factor == 2.0

    # 5. Non-allowlisted commands: MUST ALL BE REJECTED by RuleInterpreter
    non_allowlisted = [
        "caption the whole video",
        "add caption to the whole video",
        "move caption to middle",
        "make captions bold",
        "caption font size 36",
        "bleep wonder",
        "no bleep the first 'wonder' and dont bleep others",
        "unbleep wonder",
        "remove fillers",
        "remove silence",
        "remove the part about pricing",
        "actually, put the intro back",
        "the second one",
    ]
    for prompt in non_allowlisted:
        res = rule.interpret(prompt, ctx)
        assert res.status == "rejected", f"Expected rejection for '{prompt}', got: {res.status}"
        assert res.interpreted_by == "rule"
        assert "not in ruleinterpreter allowlist" in (res.rejection_reason or "").lower()


def test_interpreter_mode_llm_only_bypasses_rule_interpreter(monkeypatch: pytest.MonkeyPatch):
    """In llm_only, RuleInterpreter is bypassed entirely; missing key or errors do NOT fall back."""
    monkeypatch.setenv("INTERPRETER", "llm_only")
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert get_interpreter_mode() == "llm_only"

    # With no API key:
    groq = GroqInterpreter(api_key="", cache_enabled=False)
    ctx = InterpreterContext(duration=30.0)

    # Even for allowlisted "undo", it does NOT call RuleInterpreter!
    res_undo = groq.interpret("undo", ctx)
    assert res_undo.status == "rejected"
    assert res_undo.interpreted_by == "llm"
    assert "RuleInterpreter is bypassed in llm_only mode" in (res_undo.rejection_reason or "")

    # Even for explicit "cut 0:30-0:40", it rejects without RuleInterpreter
    res_cut = groq.interpret("cut 0:30-0:40", ctx)
    assert res_cut.status == "rejected"
    assert res_cut.interpreted_by == "llm"
    assert "RuleInterpreter is bypassed in llm_only mode" in (res_cut.rejection_reason or "")


def test_interpreter_mode_rule_only(monkeypatch: pytest.MonkeyPatch):
    """In rule_only, LLM is bypassed entirely; only RuleInterpreter allowlist is used."""
    monkeypatch.setenv("INTERPRETER", "rule_only")
    assert get_interpreter_mode() == "rule_only"

    # Even with an API key, it stays strictly local
    groq = GroqInterpreter(api_key="fake_key_should_not_be_called", cache_enabled=False)
    ctx = InterpreterContext(duration=30.0)

    # Allowlisted commands succeed
    res_undo = groq.interpret("undo", ctx)
    assert res_undo.status == "undo"
    assert res_undo.interpreted_by == "rule"

    res_cut = groq.interpret("cut 0:30-0:40", ctx)
    assert res_cut.status == "applied"
    assert res_cut.interpreted_by == "rule"

    # Non-allowlisted commands fail with rule rejection
    res_caption = groq.interpret("caption the whole video", ctx)
    assert res_caption.status == "rejected"
    assert res_caption.interpreted_by == "rule"


def test_interpreter_mode_auto(monkeypatch: pytest.MonkeyPatch):
    """In auto mode, allowlisted commands use RuleInterpreter and other commands route to LLM."""
    monkeypatch.setenv("INTERPRETER", "auto")
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert get_interpreter_mode() == "auto"

    groq = GroqInterpreter(api_key="", cache_enabled=False)
    ctx = InterpreterContext(duration=30.0)

    # Allowlisted commands succeed via RuleInterpreter fast-path
    res_undo = groq.interpret("undo", ctx)
    assert res_undo.status == "undo"
    assert res_undo.interpreted_by == "rule"

    res_cut = groq.interpret("cut 0:30-0:40", ctx)
    assert res_cut.status == "applied"
    assert res_cut.interpreted_by == "rule"

    # Non-allowlisted command attempts LLM and rejects when no key is set
    res_non_allow = groq.interpret("order pepperoni pizza", ctx)
    assert res_non_allow.status == "rejected"
    assert res_non_allow.interpreted_by == "llm"


def test_cache_hit_returns_interpreted_by_cache(tmp_path: Path):
    """Verify that cached interpretation results return interpreted_by='cache'."""
    groq = GroqInterpreter(api_key="dummy", cache_enabled=True)
    ctx = InterpreterContext(duration=20.0, time_policy="current")

    cache_key = groq._compute_cache_key("cut the boring intro", ctx)
    cached_payload = InterpretationResult(
        status="applied",
        targets=[IntentTarget(action="cut", start_time=0.0, end_time=5.0)],
        latency_ms=0.0,
        tokens_used=120,
        model_name="openai/gpt-oss-120b",
        cached=False,
    )
    groq._write_cache(cache_key, cached_payload)

    res = groq.interpret("cut the boring intro", ctx)
    assert res.cached is True
    assert res.interpreted_by == "cache"
    assert res.status == "applied"
    assert res.targets[0].end_time == 5.0


def test_operation_log_stamps_and_exposes_interpreted_by():
    """Verify that OperationLog.push stamps interpreted_by onto operations and preserves it."""
    log = OperationLog()
    op1 = CutRange(word_ids=[1, 2])
    log = log.push(op1, interpreted_by="rule")
    assert log.ops[0].interpreted_by == "rule"
    assert log.active_ops[0].interpreted_by == "rule"

    op2 = CutRange(word_ids=[3, 4], interpreted_by="llm")
    log = log.push(op2)
    assert log.ops[1].interpreted_by == "llm"

    op3 = CutRange(word_ids=[5, 6], interpreted_by="cache")
    log = log.push(op3)
    assert log.ops[2].interpreted_by == "cache"

    # has_equivalent should match regardless of interpreted_by
    op_candidate = CutRange(word_ids=[1, 2], interpreted_by="llm")
    assert log.has_equivalent(op_candidate) is True


def test_api_responses_include_interpreted_by_and_stamp_op_log(api_client: TestClient):
    """Test that every instruction response and op log entry includes interpreted_by."""
    init_res = api_client.post("/api/v1/sample-session")
    assert init_res.status_code == 200
    session_id = init_res.json()["session_id"]

    # 1. Rule allowlisted instruction: "cut the first 2 seconds"
    res1 = api_client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "cut the first 2 seconds"},
    )
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["status"] == "applied"
    assert "interpreted_by" in d1
    assert d1["interpreted_by"] == "rule"
    assert len(d1["operations"]) == 1
    assert d1["operations"][0]["interpreted_by"] == "rule"

    # Check GET /api/v1/sessions/{session_id}/log
    res_log = api_client.get(f"/api/v1/sessions/{session_id}/log")
    assert res_log.status_code == 200
    log_data = res_log.json()
    assert len(log_data["ops"]) >= 1
    assert log_data["ops"][-1]["interpreted_by"] == "rule"
    assert log_data["active_ops"][-1]["interpreted_by"] == "rule"

    # 2. Undo endpoint
    res_undo = api_client.post(f"/api/v1/sessions/{session_id}/undo")
    assert res_undo.status_code == 200
    d_undo = res_undo.json()
    assert d_undo["status"] == "applied"
    assert d_undo["interpreted_by"] == "rule"

    # 3. Redo endpoint
    res_redo = api_client.post(f"/api/v1/sessions/{session_id}/redo")
    assert res_redo.status_code == 200
    d_redo = res_redo.json()
    assert d_redo["status"] == "applied"
    assert d_redo["interpreted_by"] == "rule"

    # 4. Rejected instruction
    res_rej = api_client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "order large pizza"},
    )
    assert res_rej.status_code == 200
    d_rej = res_rej.json()
    assert d_rej["status"] == "rejected"
    assert "interpreted_by" in d_rej
    assert d_rej["interpreted_by"] in ("rule", "llm", "cache")


def test_results_json_includes_interpreted_by(tmp_path: Path):
    """Test that session runner produces results.json with interpreted_by on each step and op."""
    video_path = tmp_path / "sample_video.mp4"
    ffmpeg_bin = find_executable("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=10:sample_rate=44100",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", "10.0",
        str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    words = [
        Word(id=0, text="Hello", start=0.0, end=1.0),
        Word(id=1, text="world", start=1.0, end=2.0),
        Word(id=2, text="this", start=2.0, end=3.0),
        Word(id=3, text="is", start=3.0, end=4.0),
        Word(id=4, text="a", start=4.0, end=5.0),
        Word(id=5, text="test", start=5.0, end=6.0),
    ]
    transcript = Transcript(
        words=words,
        title="Test Presentation",
        source_duration=10.0,
    )
    transcript_path = tmp_path / "sample_transcript.json"
    transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    session_yaml = tmp_path / "test_session.yaml"
    session_yaml.write_text(
        f"""
video: {video_path.as_posix()}
transcript: {transcript_path.as_posix()}
output_video: rendered_output.mp4
steps:
  - say: "cut the first 2 seconds"
  - say: "undo"
  - say: "cut 0:02-0:04"
  - say: "render"
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    run_session(session_yaml, out_dir, render_preset="preview")

    results_file = out_dir / "results.json"
    assert results_file.is_file()

    results = json.loads(results_file.read_text(encoding="utf-8"))
    assert len(results) == 4

    for step_res in results:
        assert "interpreted_by" in step_res
        assert step_res["interpreted_by"] in ("rule", "llm", "cache")
        for op in step_res.get("operations", []):
            assert "interpreted_by" in op
            assert op["interpreted_by"] in ("rule", "llm", "cache")


def test_api_interpreter_mode_toggle(api_client: TestClient):
    """Test getting and setting interpreter mode for a session via API and per-instruction toggle."""
    init_res = api_client.post("/api/v1/sample-session")
    assert init_res.status_code == 200
    session_id = init_res.json()["session_id"]

    # 1. Default mode is 'auto'
    mode_res = api_client.get(f"/api/v1/sessions/{session_id}/interpreter-mode")
    assert mode_res.status_code == 200
    assert mode_res.json()["mode"] in ("auto", "llm_only", "rule_only")

    # 2. Set mode to 'rule_only'
    set_res = api_client.post(
        f"/api/v1/sessions/{session_id}/interpreter-mode",
        json={"mode": "rule_only"},
    )
    assert set_res.status_code == 200
    assert set_res.json()["mode"] == "rule_only"

    # Allowlisted command succeeds in rule_only
    res_allow = api_client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "cut the first 2 seconds"},
    )
    assert res_allow.status_code == 200
    assert res_allow.json()["status"] == "applied"
    assert res_allow.json()["interpreted_by"] == "rule"

    # Non-allowlisted command is rejected in rule_only
    res_non_allow = api_client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "caption the whole video"},
    )
    assert res_non_allow.status_code == 200
    assert res_non_allow.json()["status"] == "rejected"
    assert res_non_allow.json()["interpreted_by"] == "rule"

    # 3. Invalid mode returns 400
    bad_res = api_client.post(
        f"/api/v1/sessions/{session_id}/interpreter-mode",
        json={"mode": "invalid_mode"},
    )
    assert bad_res.status_code == 400

    # 4. Per-request override: send interpreter_mode="auto"
    res_override = api_client.post(
        f"/api/v1/sessions/{session_id}/instructions",
        json={"text": "undo", "interpreter_mode": "auto"},
    )
    assert res_override.status_code == 200
    assert res_override.json()["status"] == "applied"

