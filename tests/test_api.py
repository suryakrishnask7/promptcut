"""
tests/test_api.py

Unit and integration tests for FastAPI backend and local UI.
Tests all /api/v1 endpoints, range-request video streaming,
conversational instruction evaluation, clarification loops,
undo/redo, and UI mounting.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from promptcut.api.app import create_app


@pytest.fixture(scope="module")
def api_client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestApiBackendAndUI:
    """Test suite for Phase 3 FastAPI backend and UI."""

    def test_serve_ui_html(self, api_client: TestClient):
        # Root redirects to /ui
        res_root = api_client.get("/", follow_redirects=False)
        assert res_root.status_code in (307, 302)
        assert res_root.headers["location"] == "/ui"

        # /ui returns 200 HTML
        res_ui = api_client.get("/ui")
        assert res_ui.status_code == 200
        assert "text/html" in res_ui.headers["content-type"]
        assert "PromptCut" in res_ui.text
        assert "transcriptContainer" in res_ui.text
        assert "videoPlayer" in res_ui.text

    def test_sample_session_creation(self, api_client: TestClient):
        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert "job_id" in data

        session_id = data["session_id"]
        job_id = data["job_id"]

        # Check job status
        res_job = api_client.get(f"/api/v1/jobs/{job_id}")
        assert res_job.status_code == 200
        assert res_job.json()["status"] == "completed"

        # Check transcript endpoint
        res_t = api_client.get(f"/api/v1/sessions/{session_id}/transcript")
        assert res_t.status_code == 200
        transcript = res_t.json()
        assert len(transcript["words"]) == 10

        # Check initial timeline endpoint
        res_tl = api_client.get(f"/api/v1/sessions/{session_id}/timeline")
        assert res_tl.status_code == 200
        timeline = res_tl.json()
        assert timeline["total_duration"] > 0
        assert len(timeline["timeline_hash"]) == 64

        # Check initial log endpoint
        res_log = api_client.get(f"/api/v1/sessions/{session_id}/log")
        assert res_log.status_code == 200
        log_data = res_log.json()
        assert log_data["pointer"] == 0
        assert log_data["can_undo"] is False

    def test_conversational_workflow_and_clarification(self, api_client: TestClient):
        # Create fresh sample session
        init_res = api_client.post("/api/v1/sample-session")
        session_id = init_res.json()["session_id"]

        # 1. Add caption
        res1 = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "add a caption 'Welcome!' when I say welcome to the channel"},
        )
        assert res1.status_code == 200
        d1 = res1.json()
        assert d1["status"] == "applied"
        assert len(d1["operations"]) == 1
        assert d1["operations"][0]["op_type"] == "add_caption"

        # 2. Cut first 2 seconds
        res2 = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "cut the first 2 seconds"},
        )
        assert res2.status_code == 200
        d2 = res2.json()
        assert d2["status"] == "applied"
        assert d2["duration_s"] < 8.0

        # 3. Ambiguous cut: remove pricing (2 hits in sample transcript)
        res3 = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "remove the part about pricing"},
        )
        assert res3.status_code == 200
        d3 = res3.json()
        assert d3["status"] == "needs_clarification"
        assert "pricing" in d3["question"].lower()
        assert len(d3["options"]) >= 2

        # 4. Resolve clarification with answer: "the second one"
        res4 = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "the second one", "answer": "the second one"},
        )
        assert res4.status_code == 200
        d4 = res4.json()
        assert d4["status"] == "applied"
        assert d4["duration_s"] < d2["duration_s"]

        # 5. Undo
        res_undo = api_client.post(f"/api/v1/sessions/{session_id}/undo")
        assert res_undo.status_code == 200
        d_undo = res_undo.json()
        assert d_undo["status"] == "applied"
        assert "reversed" in d_undo["summary"].lower()

        # 6. Redo
        res_redo = api_client.post(f"/api/v1/sessions/{session_id}/redo")
        assert res_redo.status_code == 200
        d_redo = res_redo.json()
        assert d_redo["status"] == "applied"
        assert "re-applied" in d_redo["summary"].lower()

        # 7. Rejected instruction
        res_rej = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "make coffee please"},
        )
        assert res_rej.status_code == 200
        d_rej = res_rej.json()
        assert d_rej["status"] == "rejected"
        assert d_rej["question"] is None

    def test_range_request_video_streaming(self, api_client: TestClient):
        # Create sample session
        init_res = api_client.post("/api/v1/sample-session")
        session_id = init_res.json()["session_id"]

        # 1. Full stream request
        res_full = api_client.get(f"/api/v1/sessions/{session_id}/preview.mp4")
        assert res_full.status_code in (200, 206)
        assert res_full.headers.get("accept-ranges") == "bytes"
        assert "video/mp4" in res_full.headers["content-type"]

        # 2. Byte Range request: bytes=0-1023
        res_range = api_client.get(
            f"/api/v1/sessions/{session_id}/preview.mp4",
            headers={"Range": "bytes=0-1023"},
        )
        assert res_range.status_code == 206
        assert "bytes 0-1023/" in res_range.headers["content-range"]
        assert len(res_range.content) == 1024

        # 3. Stream original media endpoint
        res_orig = api_client.get(
            f"/api/v1/sessions/{session_id}/original.mp4",
            headers={"Range": "bytes=0-511"},
        )
        assert res_orig.status_code == 206
        assert len(res_orig.content) == 512

    def test_full_quality_render_endpoint(self, api_client: TestClient):
        init_res = api_client.post("/api/v1/sample-session")
        session_id = init_res.json()["session_id"]

        # Trigger full quality render
        res_render = api_client.post(f"/api/v1/sessions/{session_id}/render")
        assert res_render.status_code in (200, 202)
        data = res_render.json()
        assert data["status"] in ("completed", "succeeded", "queued")
        assert "rendered_url" in data

        # Stream rendered media
        res_stream = api_client.get(data["rendered_url"])
        assert res_stream.status_code in (200, 206)
        assert "video/mp4" in res_stream.headers["content-type"]

    def test_create_session_upload_and_validation(self, api_client: TestClient, tmp_path: Path):
        # 1. Test missing both file and video_path
        res_empty = api_client.post("/api/v1/sessions", data={})
        assert res_empty.status_code == 400

        # 2. Test file upload with automated transcription job (fallback or whisper)
        test_file = tmp_path / "upload_sample.mp4"
        test_file.write_bytes(b"dummy_mp4_bytes_for_upload_test" * 10)

        with open(test_file, "rb") as f:
            res_upload = api_client.post(
                "/api/v1/sessions",
                files={"file": ("upload_sample.mp4", f, "video/mp4")},
            )
        assert res_upload.status_code in (200, 202)
        d_upload = res_upload.json()
        assert "session_id" in d_upload
        assert "job_id" in d_upload

        # Verify job completed successfully without source_hash or import failure
        job_res = api_client.get(f"/api/v1/jobs/{d_upload['job_id']}")
        assert job_res.status_code == 200
        job_data = job_res.json()
        assert job_data["status"] in ("completed", "succeeded")
        assert job_data["error"] is None

        # Verify transcript is populated and available
        res_t = api_client.get(f"/api/v1/sessions/{d_upload['session_id']}/transcript")
        assert res_t.status_code == 200
        t_data = res_t.json()
        assert len(t_data["words"]) > 0

        # 3. Test upload with custom transcript sidecar file
        custom_transcript = {
            "words": [
                {"id": 0, "text": "Hello", "start": 0.0, "end": 0.5, "speaker": "S1", "confidence": 0.99},
                {"id": 1, "text": "world", "start": 0.6, "end": 1.0, "speaker": "S1", "confidence": 0.99},
            ],
            "title": "Custom Fixture",
            "source_duration": 1.5,
        }
        transcript_path = tmp_path / "sidecar.json"
        transcript_path.write_text(json.dumps(custom_transcript), encoding="utf-8")

        with open(test_file, "rb") as f_vid, open(transcript_path, "rb") as f_tr:
            res_sidecar = api_client.post(
                "/api/v1/sessions",
                files={
                    "file": ("upload_sample2.mp4", f_vid, "video/mp4"),
                    "transcript_file": ("sidecar.json", f_tr, "application/json"),
                },
            )
        assert res_sidecar.status_code in (200, 202)
        d_sidecar = res_sidecar.json()
        res_sc_t = api_client.get(f"/api/v1/sessions/{d_sidecar['session_id']}/transcript")
        assert res_sc_t.status_code == 200
        sc_words = res_sc_t.json()["words"]
        assert len(sc_words) == 2
        assert sc_words[0]["text"] == "Hello"
        assert sc_words[1]["text"] == "world"

        # 4. Test nonexistent local path -> 400
        res_nonexistent = api_client.post(
            "/api/v1/sessions",
            json={"video_path": "nonexistent_file_path_xyz.mp4"},
        )
        assert res_nonexistent.status_code == 400

    def test_clarification_interrupted_by_unrelated_message(self, api_client: TestClient):
        """
        Verify:
        (1) Pending clarification is stored in session state.
        (2) An unrelated message arriving in between clears the pending clarification
            and processes the new instruction cleanly.
        (3) Cost metrics (tokens_used, cost_usd, cost_per_video_minute) are exposed.
        """
        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # Step 1: Issue ambiguous request -> NeedsClarification
        res_ambig = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "remove the part about pricing"},
        )
        assert res_ambig.status_code == 200
        d_ambig = res_ambig.json()
        assert d_ambig["status"] == "needs_clarification"
        assert "pricing" in d_ambig["question"].lower()
        assert "cost_per_video_minute" in d_ambig

        # Step 2: Send unrelated instruction instead of answering clarification
        res_unrelated = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "remove filler words"},
        )
        assert res_unrelated.status_code == 200
        d_unrelated = res_unrelated.json()
        # Unrelated instruction must be applied cleanly
        assert d_unrelated["status"] == "applied"
        assert "filler" in d_unrelated["summary"].lower()
        assert "cost_per_video_minute" in d_unrelated

        # Step 3: Verify that sending "the second one" now is rejected or does not refer to stale pricing
        res_stale = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "the second one"},
        )
        assert res_stale.status_code == 200
        d_stale = res_stale.json()
        # Since pending clarification was cleared by the unrelated message, "the second one" cannot apply to pricing
        assert d_stale["status"] == "rejected"

    def test_word_edit_reflected_in_captions(self, api_client: TestClient):
        """
        Verify:
        If a word is corrected in the transcript (e.g. pronunciation / ASR fix),
        it immediately reflects in existing captions anchored to that word.
        """
        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # Step 1: Add caption to word 0 ("welcome")
        res_cap = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "add a caption 'WELCOME' when I say welcome"},
        )
        assert res_cap.status_code == 200
        assert res_cap.json()["status"] == "applied"

        # Check initial caption in timeline
        res_tl1 = api_client.get(f"/api/v1/sessions/{session_id}/timeline")
        tl1 = res_tl1.json()
        assert len(tl1["captions"]) == 1
        initial_hash = tl1["timeline_hash"]
        assert "WELCOME" in tl1["captions"][0]["text"].upper()

        # Step 2: Correct word 0 in transcript ("welcome" -> "NEXUS")
        res_edit = api_client.put(
            f"/api/v1/sessions/{session_id}/transcript/words/0",
            json={"text": "nexus"},
        )
        assert res_edit.status_code == 200
        edit_data = res_edit.json()
        assert edit_data["status"] == "success"
        assert edit_data["new_text"] == "nexus"
        assert edit_data["timeline_hash"] != initial_hash

        # Step 3: Verify the caption text in timeline updated to reflect "NEXUS"
        res_tl2 = api_client.get(f"/api/v1/sessions/{session_id}/timeline")
        tl2 = res_tl2.json()
        assert len(tl2["captions"]) == 1
        updated_caption = tl2["captions"][0]
        assert "NEXUS" in updated_caption["text"].upper()

    def test_delete_and_edit_operation_via_chat(self, api_client: TestClient):
        """
        Verify:
        (1) Sending 'delete #0' deactivates/removes operation #0 from timeline.
        (2) Sending 'change caption #0 to 'NEW TITLE'' edits the caption text using natural language.
        (3) Sending 'delete #0' again toggles it back.
        """
        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # Push caption op #0
        res_cap = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "add a caption 'INITIAL' when I say welcome"},
        )
        assert res_cap.status_code == 200

        # Check caption exists in timeline
        tl_res1 = api_client.get(f"/api/v1/sessions/{session_id}/timeline")
        assert len(tl_res1.json()["captions"]) == 1
        assert "INITIAL" in tl_res1.json()["captions"][0]["text"].upper()

        # Edit caption text via natural language: change caption #0 to 'UPDATED TITLE'
        res_edit = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "change caption #0 to 'UPDATED TITLE'"},
        )
        assert res_edit.status_code == 200
        assert res_edit.json()["status"] == "applied"
        assert "UPDATED TITLE" in res_edit.json()["summary"]

        tl_res2 = api_client.get(f"/api/v1/sessions/{session_id}/timeline")
        assert len(tl_res2.json()["captions"]) == 1
        assert "UPDATED TITLE" in tl_res2.json()["captions"][0]["text"].upper()

        # Delete operation #0 via chat: delete #0
        res_del = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "delete #0"},
        )
        assert res_del.status_code == 200
        d_del = res_del.json()
        assert d_del["status"] == "applied"
        assert "removed/deactivated" in d_del["summary"]

        # Timeline captions should now be 0 (deactivated)
        tl_res3 = api_client.get(f"/api/v1/sessions/{session_id}/timeline")
        assert len(tl_res3.json()["captions"]) == 0

    def test_bleep_and_selective_unbleep_via_chat(self, api_client: TestClient):
        """Test bleeping specific words and selectively unbleeping via natural language."""
        # 1. Create a sample session with "pricing" at words 4 and 7
        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # Rename word 4 and word 7 to "wonder" to match user's exact example
        api_client.put(f"/api/v1/sessions/{session_id}/transcript/words/4", json={"text": "wonder"})
        api_client.put(f"/api/v1/sessions/{session_id}/transcript/words/7", json={"text": "wonder"})

        # 2. User says: bleep "wonder"
        res_bleep_all = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": 'bleep "wonder"'},
        )
        assert res_bleep_all.status_code == 200
        data_all = res_bleep_all.json()
        assert data_all["status"] == "applied"
        assert "Bleeped all occurrences of 'wonder'" in data_all["summary"]

        # Verify both segments containing word 4 and word 7 are bleeped in timeline
        tl1 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        bleeped_wids_1 = {
            wid
            for seg in tl1["segments"]
            if seg["is_bleeped"]
            for wid in seg["source_word_ids"]
        }
        assert 4 in bleeped_wids_1
        assert 7 in bleeped_wids_1

        # 3. User says: "no bleep the first 'wonder' and dont bleep others"
        res_refine = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "no bleep the first 'wonder' and dont bleep others"},
        )
        assert res_refine.status_code == 200
        data_refine = res_refine.json()
        assert data_refine["status"] == "applied"
        assert "removed bleep from others" in data_refine["summary"].lower() or "first" in data_refine["summary"].lower()

        # Verify in timeline: word 4 is bleeped, but word 7 is NOT bleeped
        tl2 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        bleeped_wids_2 = {
            wid
            for seg in tl2["segments"]
            if seg["is_bleeped"]
            for wid in seg["source_word_ids"]
        }
        assert 4 in bleeped_wids_2
        assert 7 not in bleeped_wids_2

        # 4. User says: "unbleep 'wonder'" (removes all remaining bleeps)
        res_unbleep = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "unbleep 'wonder'"},
        )
        assert res_unbleep.status_code == 200
        tl3 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        bleeped_wids_3 = {
            wid
            for seg in tl3["segments"]
            if seg["is_bleeped"]
            for wid in seg["source_word_ids"]
        }
        assert len(bleeped_wids_3) == 0

    def test_font_size_default_step_and_clarification(self, api_client: TestClient):
        """Test default font size step (+4), changing default step, and clarification resolution."""
        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        # Add initial caption
        res_cap = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "add a caption 'Welcome' when I say welcome"},
        )
        assert res_cap.status_code == 200
        tl0 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        assert tl0["captions"][0]["font_size"] == 32

        # 1. "increase the font size for the captions" -> should increase by default +4 to 36
        res_inc = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "increase the font size for the captions"},
        )
        assert res_inc.status_code == 200
        assert res_inc.json()["status"] == "applied"
        assert "36 pt" in res_inc.json()["summary"]
        tl1 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        assert tl1["captions"][0]["font_size"] == 36

        # 2. "change the default increase/decrease to 5"
        res_step = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "change the default increase/decrease to 5"},
        )
        assert res_step.status_code == 200
        assert res_step.json()["status"] == "applied"
        assert "5 pt" in res_step.json()["summary"]

        # 3. "increase font size" again -> should now increase by +5 (36 + 5 = 41)
        res_inc2 = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "increase font size"},
        )
        assert res_inc2.status_code == 200
        assert res_inc2.json()["status"] == "applied"
        assert "41 pt" in res_inc2.json()["summary"]
        tl2 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        assert tl2["captions"][0]["font_size"] == 41

        # 4. Clarification resolution: simulate a pending font size clarification
        from promptcut.api.routes import _sessions
        session = _sessions[session_id]
        session.pending_clarification = {
            "action": "font_size",
            "question": "How would you like to increase the font size for the captions?",
            "options": ["Increase by a specific number of points (e.g., +2)", "Set to a specific size (e.g., 36)"],
        }
        # User provides answer "36"
        res_clarify = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "36", "answer": "36"},
        )
        assert res_clarify.status_code == 200
        assert res_clarify.json()["status"] == "applied"
        assert "36 pt" in res_clarify.json()["summary"]
        tl3 = api_client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        assert tl3["captions"][0]["font_size"] == 36

    def test_instruction_latency_under_3_seconds(self, api_client: TestClient):
        import time

        res = api_client.post("/api/v1/sample-session")
        assert res.status_code == 200
        session_id = res.json()["session_id"]

        start_time = time.perf_counter()
        res_inst = api_client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"text": "add captions in yellow"},
        )
        elapsed = time.perf_counter() - start_time

        assert res_inst.status_code == 200
        assert res_inst.json()["status"] == "applied"
        # IncuBrix assessment scale requirement: instructions must respond under 3 seconds
        assert elapsed < 3.0, f"Instruction response time took {elapsed:.2f}s, exceeding 3.0s limit"






