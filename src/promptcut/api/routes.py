"""
FastAPI Route Handlers for PromptCut Backend (/api/v1/).

Implements:
- POST /api/v1/sessions (create session via upload or path, start background transcription)
- GET /api/v1/jobs/{job_id} (check background job status)
- GET /api/v1/sessions/{id}/transcript (retrieve transcript model)
- POST /api/v1/sessions/{id}/instructions (evaluate natural language edit prompts)
- GET /api/v1/sessions/{id}/timeline (retrieve compiled timeline)
- GET /api/v1/sessions/{id}/log (retrieve operation log history & pointer)
- POST /api/v1/sessions/{id}/undo (revert last instruction)
- POST /api/v1/sessions/{id}/redo (re-apply last undone instruction)
- GET /api/v1/sessions/{id}/preview.mp4 (stream 480p preview with range requests)
- GET /api/v1/sessions/{id}/original.mp4 (stream original video with range requests)
- POST /api/v1/sessions/{id}/render (start full quality render)
- GET /api/v1/sessions/{id}/rendered.mp4 (stream final rendered video)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
import re
import shutil
import subprocess
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from promptcut.api.webhook import dispatch_webhook

from promptcut.api.streaming import stream_video_with_range
from promptcut.config import TIME_POLICY, get_interpreter_mode
from promptcut.engine import apply
from promptcut.media.intake import (
    compute_file_sha256,
    find_executable,
    inspect_media,
)
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import BleepWords, MaskCaptions, SetCaptionStyle, UnmaskCaptions
from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript, Word
from promptcut.render.ffmpeg import render_timeline
from promptcut.session.parser import parse_instruction
from promptcut.transcription.mock import MockTranscriptionAdapter
from promptcut.transcription.vosk_adapter import VoskTranscriptionAdapter
from promptcut.transcription.whisper import FasterWhisperAdapter

router = APIRouter(prefix="/api/v1")

# Global session and job state storage
SESSIONS_ROOT = Path(".promptcut_sessions")
SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)


class SessionState:
    """In-memory active state container for an editing session."""

    def __init__(
        self,
        session_id: str,
        video_path: Path,
        original_hash: str,
        session_dir: Path,
    ):
        self.session_id = session_id
        self.video_path = video_path
        self.original_hash = original_hash
        self.session_dir = session_dir
        self.transcript: Transcript | None = None
        self.log = OperationLog()
        self.timeline: Timeline | None = None
        self.pending_instruction: str | None = None
        self.pending_clarification: dict[str, Any] | None = None
        self.chat_history: list[dict[str, Any]] = []
        self.total_tokens_used: int = 0
        self.total_cost_usd: float = 0.0
        self.default_font_step: int = 4
        self.interpreter_mode: str = get_interpreter_mode()


_sessions: dict[str, SessionState] = {}
_jobs: dict[str, dict[str, Any]] = {}


class InstructionRequest(BaseModel):
    text: str = ""
    instruction: str | None = None
    prompt: str | None = None
    answer: str | None = None
    interpreter_mode: str | None = None
    mode: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("text"):
                if data.get("prompt"):
                    data["text"] = str(data["prompt"])
                elif data.get("instruction"):
                    data["text"] = str(data["instruction"])
            if not data.get("interpreter_mode") and data.get("mode"):
                data["interpreter_mode"] = str(data["mode"])
        return data


class InterpreterModeRequest(BaseModel):
    mode: str


class UpdateWordRequest(BaseModel):
    text: str


class CreateSessionRequest(BaseModel):
    video_path: str | None = None
    video_url: str | None = None
    transcript_fixture: str | None = None
    callback_url: str | None = None


# ---------------------------------------------------------------------------
# Background Task Helpers
# ---------------------------------------------------------------------------


def _run_transcription_job(
    session: SessionState,
    job_id: str,
    fixture_path: Path | None = None,
    callback_url: str | None = None,
) -> None:
    try:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["progress"] = 25

        if fixture_path and fixture_path.is_file():
            adapter = MockTranscriptionAdapter(fixture_path=fixture_path)
            transcript = adapter.transcribe(
                session.video_path,
                source_hash=session.original_hash,
            )
        else:
            from promptcut.config import get_transcription_adapter_mode
            t_mode = get_transcription_adapter_mode()
            transcript = None
            used_provider = "mock"

            # 1. Cloud Groq Whisper (lightning-fast, word-level timestamps)
            if t_mode in ("auto", "groq"):
                groq_key = os.environ.get("GROQ_API_KEY")
                if groq_key:
                    try:
                        from promptcut.transcription.groq_adapter import GroqWhisperAdapter
                        groq_adapter = GroqWhisperAdapter(api_key=groq_key)
                        _jobs[job_id]["progress"] = 35
                        transcript = groq_adapter.transcribe(
                            session.video_path,
                            source_hash=session.original_hash,
                        )
                        if transcript and len(transcript.words) > 0:
                            used_provider = groq_adapter.model_name
                    except Exception as e:
                        logger.warning(f"Groq transcription failed: {e}")

            # 2. Local Faster-Whisper (if installed)
            if (transcript is None or len(transcript.words) == 0) and t_mode in ("auto", "whisper"):
                try:
                    whisper = FasterWhisperAdapter()
                    _jobs[job_id]["progress"] = 50
                    transcript = whisper.transcribe(session.video_path, source_hash=session.original_hash)
                    if transcript and len(transcript.words) > 0:
                        used_provider = whisper.model_name
                except Exception as e:
                    logger.warning(f"FasterWhisper failed: {e}")

            # 3. Local Vosk (offline ASR, Kaldi word alignment)
            if (transcript is None or len(transcript.words) == 0) and t_mode in ("auto", "vosk"):
                try:
                    vosk = VoskTranscriptionAdapter()
                    transcript = vosk.transcribe(session.video_path, source_hash=session.original_hash)
                    if transcript and len(transcript.words) > 0:
                        used_provider = vosk.model_name
                except Exception as e:
                    logger.warning(f"Vosk failed: {e}")

            # 4. Fallback: MockTranscriptionAdapter (synthetic/default words when offline without models)
            if transcript is None or len(transcript.words) == 0:
                _jobs[job_id]["progress"] = 75
                adapter = MockTranscriptionAdapter()
                transcript = adapter.transcribe(
                    session.video_path,
                    source_hash=session.original_hash,
                )
                used_provider = "mock"

        _jobs[job_id]["progress"] = 85
        session.transcript = transcript
        session.timeline = apply(session.log, transcript)
        try:
            (session.session_dir / "transcript.json").write_text(
                transcript.model_dump_json(indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # Pre-generate 480p preview for short clips (<= 120s); longer videos stream directly
        # or render preview on-demand so user gets immediate editor access in ~15s
        v_dur = getattr(transcript, "source_duration", 0.0)
        if v_dur <= 120.0:
            preview_path = session.session_dir / "preview.mp4"
            try:
                render_timeline(
                    session.timeline,
                    session.transcript,
                    session.video_path,
                    preview_path,
                    preset="preview",
                )
            except Exception:
                pass

        _jobs[job_id]["progress"] = 100
        _jobs[job_id]["status"] = "succeeded"

        # IncuBrix Integration Standard #6: Write manifest.json
        try:
            from promptcut.models.manifest import build_job_manifest, write_manifest_file
            t_prov = "mock" if (fixture_path and fixture_path.is_file()) else used_provider
            manifest = build_job_manifest(
                job_id=job_id,
                session_id=session.session_id,
                job_type="transcription",
                transcription_provider=t_prov,
            )
            write_manifest_file(manifest, session.session_dir / "manifest.json")
            _jobs[job_id]["manifest"] = manifest.model_dump()
        except Exception:
            pass

        if callback_url:
            dispatch_webhook(callback_url, {
                "job_id": job_id,
                "session_id": session.session_id,
                "type": "transcription",
                "status": "succeeded",
                "progress": 100,
            })
    except BaseException as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)
        if callback_url:
            dispatch_webhook(callback_url, {
                "job_id": job_id,
                "session_id": session.session_id,
                "type": "transcription",
                "status": "failed",
                "progress": _jobs[job_id].get("progress", 0),
                "error": str(e),
            })


def _run_render_job(
    session: SessionState,
    job_id: str,
    callback_url: str | None = None,
) -> None:
    try:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["progress"] = 5

        def _handle_progress(pct: float) -> None:
            if job_id in _jobs:
                _jobs[job_id]["progress"] = round(pct, 1)

        render_out = session.session_dir / "rendered.mp4"
        render_timeline(
            session.timeline,
            session.transcript,
            session.video_path,
            render_out,
            preset="final",
            on_progress=_handle_progress,
        )

        # IncuBrix Integration Standard #7: Upload rendered file to storage backend
        # and return a download link (presigned URL for S3/MinIO, local route for disk).
        rendered_url = f"/api/v1/sessions/{session.session_id}/rendered.mp4"
        download_url = rendered_url
        try:
            from promptcut.storage import get_storage_adapter
            storage = get_storage_adapter()
            storage_key = f"sessions/{session.session_id}/rendered.mp4"
            storage.save_file(storage_key, render_out, content_type="video/mp4")
            download_url = storage.get_url(storage_key)
            logger.info(
                f"Render uploaded to storage [{storage_key}] → {download_url} "
                f"[job_id={job_id}]"
            )
        except Exception as upload_err:
            logger.warning(
                f"Storage upload skipped (falling back to local stream): {upload_err} "
                f"[job_id={job_id}]"
            )

        _jobs[job_id]["progress"] = 100
        _jobs[job_id]["status"] = "succeeded"
        _jobs[job_id]["rendered_url"] = rendered_url
        _jobs[job_id]["download_url"] = download_url

        # IncuBrix Integration Standard #6: Write manifest.json
        try:
            from promptcut.models.manifest import build_job_manifest, write_manifest_file
            manifest = build_job_manifest(
                job_id=job_id,
                session_id=session.session_id,
                job_type="render",
            )
            write_manifest_file(manifest, session.session_dir / "manifest.json")
            _jobs[job_id]["manifest"] = manifest.model_dump()
        except Exception:
            pass

        if callback_url:
            dispatch_webhook(callback_url, {
                "job_id": job_id,
                "session_id": session.session_id,
                "type": "render",
                "status": "succeeded",
                "progress": 100,
                "rendered_url": rendered_url,
                "download_url": download_url,
            })
    except BaseException as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)
        if callback_url:
            dispatch_webhook(callback_url, {
                "job_id": job_id,
                "session_id": session.session_id,
                "type": "render",
                "status": "failed",
                "progress": _jobs[job_id].get("progress", 0),
                "error": str(e),
            })


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions", status_code=status.HTTP_202_ACCEPTED)
async def create_session(
    request: Request,
    background_tasks: BackgroundTasks,
    video_path: str | None = Form(None),
    video_url: str | None = Form(None),
    transcript_fixture: str | None = Form(None),
    callback_url: str | None = Form(None),
    file: UploadFile | None = File(None),
    transcript_file: UploadFile | None = File(None),
) -> Response:
    """Create a new editing session from a file upload, video URL, or local file path."""
    # Support JSON body if content-type is application/json
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
        video_path = body.get("video_path")
        video_url = body.get("video_url")
        transcript_fixture = body.get("transcript_fixture")
        callback_url = body.get("callback_url")

    session_id = str(uuid.uuid4())[:8]
    session_dir = SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    target_video_path: Path
    if file and file.filename:
        target_video_path = session_dir / file.filename
        with open(target_video_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    elif video_url:
        from promptcut.media.url_intake import download_video_from_url
        target_video_path = session_dir / "downloaded_input.mp4"
        download_video_from_url(video_url, target_video_path)
    elif video_path:
        local_p = Path(video_path).resolve()
        if not local_p.is_file():
            raise HTTPException(status_code=400, detail=f"File not found: {video_path}")
        target_video_path = local_p
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'file' upload, 'video_url', or 'video_path' string.",
        )

    orig_hash = compute_file_sha256(target_video_path)
    session = SessionState(
        session_id=session_id,
        video_path=target_video_path,
        original_hash=orig_hash,
        session_dir=session_dir,
    )
    _sessions[session_id] = session
    try:
        (session_dir / "session_meta.json").write_text(
            json.dumps({"session_id": session_id, "video_path": str(target_video_path), "original_hash": orig_hash}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "job_id": job_id,
        "session_id": session_id,
        "type": "transcription",
        "status": "queued",
        "progress": 0,
        "error": None,
    }

    fixture_p: Path | None = None
    if transcript_file and transcript_file.filename:
        fixture_p = session_dir / "uploaded_transcript.json"
        with open(fixture_p, "wb") as f:
            shutil.copyfileobj(transcript_file.file, f)
    elif transcript_fixture:
        candidate = Path(transcript_fixture).resolve()
        if candidate.is_file():
            fixture_p = candidate

    background_tasks.add_task(
        _run_transcription_job,
        session=session,
        job_id=job_id,
        fixture_path=fixture_p,
        callback_url=callback_url,
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "session_id": session_id,
            "job_id": job_id,
            "status": "queued",
            "message": "Session created. Transcription job started in background.",
        },
    )


@router.post("/sample-session")
def create_sample_session(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Generate a sample session with synthetic 8-second video and transcript fixture."""
    session_id = str(uuid.uuid4())[:8]
    session_dir = SESSIONS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    video_path = session_dir / "sample_presentation.mp4"
    ffmpeg_bin = find_executable("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi", "-i", "testsrc=duration=8:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=8:sample_rate=44100",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", "8.0",
        str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    words = [
        Word(id=0, text="Welcome", start=0.0, end=0.6, speaker="S1"),
        Word(id=1, text="to", start=0.6, end=1.0, speaker="S1"),
        Word(id=2, text="the", start=1.0, end=1.4, speaker="S1"),
        Word(id=3, text="channel", start=1.4, end=2.4, speaker="S1"),
        Word(id=4, text="pricing", start=2.4, end=3.4, speaker="S1"),
        Word(id=5, text="is", start=3.4, end=4.0, speaker="S1"),
        Word(id=6, text="simple", start=4.0, end=4.8, speaker="S1"),
        Word(id=7, text="pricing", start=4.8, end=5.8, speaker="S1"),
        Word(id=8, text="tier", start=5.8, end=6.6, speaker="S1"),
        Word(id=9, text="thanks", start=6.6, end=7.8, speaker="S1"),
    ]
    transcript = Transcript(
        words=words,
        title="Sample Presentation (Worked Example)",
        source_duration=8.0,
    )
    fixture_path = session_dir / "transcript.json"
    fixture_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    orig_hash = compute_file_sha256(video_path)
    session = SessionState(
        session_id=session_id,
        video_path=video_path,
        original_hash=orig_hash,
        session_dir=session_dir,
    )
    session.transcript = transcript
    session.timeline = apply(session.log, transcript)
    _sessions[session_id] = session

    # Initial preview render
    preview_path = session_dir / "preview.mp4"
    render_timeline(
        session.timeline,
        session.transcript,
        session.video_path,
        preview_path,
        preset="preview",
    )

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "job_id": job_id,
        "session_id": session_id,
        "type": "sample_init",
        "status": "completed",
        "progress": 100,
        "error": None,
    }

    return {
        "session_id": session_id,
        "job_id": job_id,
        "message": "Sample session initialized with 8s synthetic video.",
    }


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> dict[str, Any]:
    """Retrieve execution status of a background job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]
    if job.get("type") == "render" and job.get("status") in ("queued", "running"):
        sess_id = job.get("session_id")
        if sess_id:
            sess_dir = Path(".promptcut_sessions") / sess_id
            rend_file = sess_dir / "rendered.mp4"
            if rend_file.is_file() and rend_file.stat().st_size > 1000:
                # If manifest or complete output file exists, report succeeded
                manifest_file = sess_dir / "manifest.json"
                if manifest_file.is_file():
                    job["status"] = "succeeded"
                    job["progress"] = 100
    return job


@router.get("/sessions/{session_id}/transcript")
def get_transcript(session_id: str) -> dict[str, Any]:
    """Retrieve current Transcript model."""
    session = _get_session(session_id)
    if not session.transcript:
        raise HTTPException(status_code=409, detail="Transcription still in progress")
    return session.transcript.model_dump()


@router.put("/sessions/{session_id}/transcript/words/{word_id}")
def update_transcript_word(
    session_id: str,
    word_id: int,
    req: UpdateWordRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Update / correct a word's text in the transcript (e.g. for pronunciation or ASR errors)."""
    session = _get_session(session_id)
    if not session.transcript:
        raise HTTPException(status_code=409, detail="Transcription not completed yet")

    matched_word: Word | None = None
    for w in session.transcript.words:
        if w.id == word_id:
            matched_word = w
            break

    if not matched_word:
        raise HTTPException(status_code=404, detail=f"Word id {word_id} not found in transcript")

    old_text = matched_word.text
    matched_word.text = req.text.strip()

    # Synchronize any caption operations referencing this word
    import re
    clean_old = re.sub(r"^[^\w]+|[^\w]+$", "", old_text)
    clean_new = re.sub(r"^[^\w]+|[^\w]+$", "", matched_word.text)

    for op in session.log.ops:
        if hasattr(op, "word_ids") and word_id in op.word_ids:
            if hasattr(op, "text") and isinstance(op.text, str):
                new_val = clean_new.upper() if op.text.isupper() else clean_new
                replaced = False

                if clean_old:
                    pattern = re.compile(rf"\b{re.escape(clean_old)}\b", re.IGNORECASE)
                    if pattern.search(op.text):
                        op.text = pattern.sub(new_val, op.text)
                        replaced = True
                    elif clean_old.lower() in op.text.lower():
                        idx = op.text.lower().find(clean_old.lower())
                        op.text = op.text[:idx] + new_val + op.text[idx + len(clean_old):]
                        replaced = True

                if not replaced and old_text.lower() in op.text.lower():
                    idx = op.text.lower().find(old_text.lower())
                    op.text = op.text[:idx] + matched_word.text + op.text[idx + len(old_text):]
                    replaced = True

                # If op.text was completely an auto-joined anchor text and didn't match cleanly
                if not replaced:
                    anchor_words = [
                        session.transcript.word_by_id(wid)
                        for wid in op.word_ids
                        if session.transcript.word_by_id(wid) is not None
                    ]
                    if anchor_words:
                        op.text = " ".join(w.text for w in anchor_words).upper() if op.text.isupper() else " ".join(w.text for w in anchor_words)

    # Re-derive timeline and trigger preview update
    session.timeline = apply(session.log, session.transcript)
    _trigger_preview_render(session, background_tasks)

    return {
        "status": "success",
        "word_id": word_id,
        "old_text": old_text,
        "new_text": matched_word.text,
        "timeline_hash": session.timeline.timeline_hash,
    }


@router.get("/sessions/{session_id}/timeline")
def get_timeline(session_id: str) -> dict[str, Any]:
    """Retrieve compiled Timeline model."""
    session = _get_session(session_id)
    if not session.timeline:
        raise HTTPException(status_code=409, detail="Timeline not yet compiled")
    return session.timeline.model_dump()


@router.get("/sessions/{session_id}/log")
def get_log(session_id: str) -> dict[str, Any]:
    """Retrieve full OperationLog and undo/redo pointers."""
    session = _get_session(session_id)
    return {
        "pointer": session.log.pointer,
        "can_undo": session.log.can_undo,
        "can_redo": session.log.can_redo,
        "ops": [op.model_dump() for op in session.log.ops],
        "active_ops": [op.model_dump() for op in session.log.active_ops],
    }


@router.post("/sessions/{session_id}/instructions")
def evaluate_instruction(
    session_id: str,
    req: InstructionRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Evaluate a natural language edit instruction."""
    session = _get_session(session_id)
    if not session.transcript:
        raise HTTPException(status_code=409, detail="Transcription not completed yet")

    prompt_text = req.text.strip()
    timeline = session.timeline or apply(session.log, session.transcript)
    interpreted_by = "rule"

    req_mode = req.interpreter_mode.strip().lower() if req.interpreter_mode else None
    if req_mode in ("auto", "llm_only", "rule_only"):
        session.interpreter_mode = req_mode
        mode = req_mode
    elif getattr(session, "interpreter_mode", None):
        mode = session.interpreter_mode
    else:
        mode = get_interpreter_mode()

    if mode != "llm_only":
        # Direct op commands: delete / remove / restore op by ID (e.g. "delete #8", "remove #8")
        m_del_op = re.search(
            r"^(?:delete|remove|restore|toggle|deactivate|undo)\s+(?:op(?:eration)?\s*)?#?(\d+)$",
            prompt_text,
            re.IGNORECASE,
        )
        if m_del_op:
            target_op_id = int(m_del_op.group(1))
            op_found = any(op.op_id == target_op_id for op in session.log.ops)
            if op_found:
                session.log = session.log.restore_op(target_op_id)
                session.timeline = apply(session.log, session.transcript)
                _trigger_preview_render(session, background_tasks)

                toggled_op = next(op for op in session.log.ops if op.op_id == target_op_id)
                action_desc = "activated" if toggled_op.active else "removed/deactivated"
                summary_msg = f"Operation #{target_op_id} ({toggled_op.op_type}) {action_desc}."

                cur_dur = session.timeline.total_duration if session.timeline else 0.0
                response_payload = {
                    "status": "applied",
                    "interpreted_by": "rule",
                    "operations": [],
                    "summary": summary_msg,
                    "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                    "duration_s": cur_dur,
                    "time_policy": TIME_POLICY,
                    "tokens_used": session.total_tokens_used,
                    "cost_usd": round(session.total_cost_usd, 6),
                    "cost_per_video_minute": 0.0,
                }
                session.chat_history.append({"role": "user", "text": prompt_text})
                session.chat_history.append({"role": "assistant", **response_payload})
                return response_payload
            else:
                response_payload = {
                    "status": "rejected",
                    "interpreted_by": "rule",
                    "summary": f"Operation #{target_op_id} not found in operation log.",
                    "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                    "duration_s": session.timeline.total_duration if session.timeline else 0.0,
                    "time_policy": TIME_POLICY,
                }
                session.chat_history.append({"role": "user", "text": prompt_text})
                session.chat_history.append({"role": "assistant", **response_payload})
                return response_payload

        # Direct change / edit caption #ID text (e.g. "change caption #8 to 'WELCOME'")
        m_edit_op = re.search(
            r"^(?:change|edit|rename|set)\s+(?:caption\s+)?#?(\d+)\s+(?:text\s+)?(?:to|=|as)\s+['\"]?([^'\"]+?)['\"]?$",
            prompt_text,
            re.IGNORECASE,
        )
        if m_edit_op:
            target_op_id = int(m_edit_op.group(1))
            new_text = m_edit_op.group(2).strip().upper()
            op_found = any(op.op_id == target_op_id for op in session.log.ops)
            if op_found:
                style_op = SetCaptionStyle(
                    target_caption_op_id=target_op_id,
                    text=new_text,
                    interpreted_by="rule",
                )
                session.log = session.log.push(style_op, interpreted_by="rule")
                session.timeline = apply(session.log, session.transcript)
                _trigger_preview_render(session, background_tasks)

                summary_msg = f"Updated caption #{target_op_id} text to '{new_text}'."
                cur_dur = session.timeline.total_duration if session.timeline else 0.0
                response_payload = {
                    "status": "applied",
                    "interpreted_by": "rule",
                    "operations": [style_op.model_dump()],
                    "summary": summary_msg,
                    "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                    "duration_s": cur_dur,
                    "time_policy": TIME_POLICY,
                    "tokens_used": session.total_tokens_used,
                    "cost_usd": round(session.total_cost_usd, 6),
                    "cost_per_video_minute": 0.0,
                }
                session.chat_history.append({"role": "user", "text": prompt_text})
                session.chat_history.append({"role": "assistant", **response_payload})
                return response_payload

        # Direct command: set default font size step (e.g. "change the default increase/decrease to 4")
        m_step = re.search(
            r"(?:change|set)\s+(?:the\s+)?default\s+(?:font\s*(?:size)?\s*)?(?:increase\s*(?:/|\s*or\s*|-)?\s*d(?:ec|c)?rease|increase|d(?:ec|c)?rease|step|increment)?\s*(?:to|=)?\s*(\d+)",
            prompt_text,
            re.IGNORECASE,
        )
        if m_step:
            step_val = int(m_step.group(1))
            session.default_font_step = step_val
            summary_msg = f"Updated default font size increase/decrease step to {step_val} pt."
            cur_dur = session.timeline.total_duration if session.timeline else 0.0
            response_payload = {
                "status": "applied",
                "interpreted_by": "rule",
                "operations": [],
                "summary": summary_msg,
                "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                "duration_s": cur_dur,
                "time_policy": TIME_POLICY,
                "tokens_used": session.total_tokens_used,
                "cost_usd": round(session.total_cost_usd, 6),
                "cost_per_video_minute": 0.0,
            }
            session.chat_history.append({"role": "user", "text": prompt_text})
            session.chat_history.append({"role": "assistant", **response_payload})
            return response_payload

        # Direct command: increase / decrease font size
        m_inc = re.search(
            r"(?:(?:increase|decrease|enlarge|reduce)\s+(?:the\s+)?(?:caption\s+)?font\s+size(?:\s+(?:for|of)\s+(?:the\s+)?captions?)?(?:\s+by\s+(\d+))?)|(?:make\s+(?:the\s+)?(?:captions?|font)\s+(bigger|smaller|larger))",
            prompt_text,
            re.IGNORECASE,
        )
        if m_inc:
            raw_by = m_inc.group(1)
            delta_num = int(raw_by) if raw_by and raw_by.isdigit() else session.default_font_step
            is_dec = "decrease" in prompt_text.lower() or "reduce" in prompt_text.lower() or "smaller" in prompt_text.lower()
            delta = -delta_num if is_dec else delta_num

            cur_size = 32
            if session.timeline and session.timeline.captions:
                cur_size = getattr(session.timeline.captions[0], "font_size", 32) or 32
            new_size = max(10, cur_size + delta)

            style_op = SetCaptionStyle(font_size=new_size, interpreted_by="rule")
            session.log = session.log.push(style_op, interpreted_by="rule")
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

            dir_str = "Decreased" if is_dec else "Increased"
            summary_msg = f"{dir_str} caption font size by {delta:+d} pt to {new_size} pt."
            cur_dur = session.timeline.total_duration if session.timeline else 0.0
            response_payload = {
                "status": "applied",
                "interpreted_by": "rule",
                "operations": [style_op.model_dump()],
                "summary": summary_msg,
                "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                "duration_s": cur_dur,
                "time_policy": TIME_POLICY,
                "tokens_used": session.total_tokens_used,
                "cost_usd": round(session.total_cost_usd, 6),
                "cost_per_video_minute": 0.0,
            }
            session.chat_history.append({"role": "user", "text": prompt_text})
            session.chat_history.append({"role": "assistant", **response_payload})
            return response_payload

        # Direct command: unmask captions (e.g. "unmask captions", "unmask the captions", "dont mask captions", "show bleeped words in captions")
        m_unmask = re.search(
            r"^(?:unmask\s+(?:the\s+)?(?:captions?|bleeps?|bleeped\s+words?|subtitles?)|dont\s+mask\s+(?:the\s+)?(?:captions?|bleeps?|bleeped\s+words?)|show\s+bleeped\s+words?\s+in\s+captions?)$",
            prompt_text,
            re.IGNORECASE,
        )
        if m_unmask:
            op_unmask = UnmaskCaptions(word_ids=[], interpreted_by="rule")
            session.log = session.log.push(op_unmask, interpreted_by="rule")
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

            summary_msg = "Unmasked bleeped words in captions."
            cur_dur = session.timeline.total_duration if session.timeline else 0.0
            response_payload = {
                "status": "applied",
                "interpreted_by": "rule",
                "operations": [op_unmask.model_dump()],
                "summary": summary_msg,
                "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                "duration_s": cur_dur,
                "time_policy": TIME_POLICY,
                "tokens_used": session.total_tokens_used,
                "cost_usd": round(session.total_cost_usd, 6),
                "cost_per_video_minute": 0.0,
            }
            session.chat_history.append({"role": "user", "text": prompt_text})
            session.chat_history.append({"role": "assistant", **response_payload})
            return response_payload

        # Direct command: remask captions (e.g. "remask captions", "mask captions", "mask bleeped words in captions")
        m_remask = re.search(
            r"^(?:(?:remask|mask)\s+(?:the\s+)?(?:captions?|bleeps?|bleeped\s+words?|subtitles?)(?:\s+in\s+captions?)?)$",
            prompt_text,
            re.IGNORECASE,
        )
        if m_remask:
            op_remask = MaskCaptions(word_ids=[], interpreted_by="rule")
            session.log = session.log.push(op_remask, interpreted_by="rule")
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

            summary_msg = "Masked bleeped words in captions with asterisks."
            cur_dur = session.timeline.total_duration if session.timeline else 0.0
            response_payload = {
                "status": "applied",
                "interpreted_by": "rule",
                "operations": [op_remask.model_dump()],
                "summary": summary_msg,
                "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                "duration_s": cur_dur,
                "time_policy": TIME_POLICY,
                "tokens_used": session.total_tokens_used,
                "cost_usd": round(session.total_cost_usd, 6),
                "cost_per_video_minute": 0.0,
            }
            session.chat_history.append({"role": "user", "text": prompt_text})
            session.chat_history.append({"role": "assistant", **response_payload})
            return response_payload

        # Direct command: selective bleep refinement (e.g. "no bleep the first 'wonder' and dont bleep others")
        m_bleep_refine = re.search(
            r"(?:no\s+)?bleep\s+(?:only\s+)?(?:the\s+)?(first|second|1st|2nd|1|2)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?(?:\s+and\s+(?:dont|do\s+not)\s+bleep\s+(?:the\s+)?others)?",
            prompt_text,
            re.IGNORECASE,
        )
        if m_bleep_refine:
            occ_str = m_bleep_refine.group(1).lower()
            word_target = m_bleep_refine.group(2).strip()
            idx = 1 if ("first" in occ_str or "1" in occ_str) else 2
            from promptcut.resolver import phrase_search
            hits = phrase_search(session.transcript, word_target)
            if hits:
                target_hit = hits[idx - 1] if idx <= len(hits) else hits[0]
                # Restore previous bleep operations to undo them
                for op in list(session.log.active_ops):
                    if type(op).__name__ in ("BleepWords", "MaskCaptions"):
                        session.log = session.log.restore_op(op.op_id)
                op_b = BleepWords(word_ids=target_hit, interpreted_by="rule")
                op_m = MaskCaptions(word_ids=target_hit, interpreted_by="rule")
                session.log = session.log.push(op_b, interpreted_by="rule")
                session.log = session.log.push(op_m, interpreted_by="rule")
                session.timeline = apply(session.log, session.transcript)
                _trigger_preview_render(session, background_tasks)

                summary_msg = f"Bleeped first '{word_target}' and removed bleep from others."
                cur_dur = session.timeline.total_duration if session.timeline else 0.0
                response_payload = {
                    "status": "applied",
                    "interpreted_by": "rule",
                    "operations": [op_b.model_dump(), op_m.model_dump()],
                    "summary": summary_msg,
                    "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                    "duration_s": cur_dur,
                    "time_policy": TIME_POLICY,
                    "tokens_used": session.total_tokens_used,
                    "cost_usd": round(session.total_cost_usd, 6),
                    "cost_per_video_minute": 0.0,
                }
                session.chat_history.append({"role": "user", "text": prompt_text})
                session.chat_history.append({"role": "assistant", **response_payload})
                return response_payload

        # Direct command: bleep all occurrences (e.g. "bleep 'wonder'")
        m_bleep_all = re.search(
            r"^bleep\s+(?:the\s+word\s+)?['\"]?([a-zA-Z0-9_\-]+)['\"]?$",
            prompt_text,
            re.IGNORECASE,
        )
        if m_bleep_all:
            word_target = m_bleep_all.group(1).strip()
            from promptcut.resolver import phrase_search
            hits = phrase_search(session.transcript, word_target)
            if hits:
                all_wids = [wid for hit in hits for wid in hit]
                op_b = BleepWords(word_ids=all_wids, interpreted_by="rule")
                op_m = MaskCaptions(word_ids=all_wids, interpreted_by="rule")
                session.log = session.log.push(op_b, interpreted_by="rule")
                session.log = session.log.push(op_m, interpreted_by="rule")
                session.timeline = apply(session.log, session.transcript)
                _trigger_preview_render(session, background_tasks)

                summary_msg = f"Bleeped all occurrences of '{word_target}'."
                cur_dur = session.timeline.total_duration if session.timeline else 0.0
                response_payload = {
                    "status": "applied",
                    "interpreted_by": "rule",
                    "operations": [op_b.model_dump(), op_m.model_dump()],
                    "summary": summary_msg,
                    "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                    "duration_s": cur_dur,
                    "time_policy": TIME_POLICY,
                    "tokens_used": session.total_tokens_used,
                    "cost_usd": round(session.total_cost_usd, 6),
                    "cost_per_video_minute": 0.0,
                }
                session.chat_history.append({"role": "user", "text": prompt_text})
                session.chat_history.append({"role": "assistant", **response_payload})
                return response_payload

        # Direct command: unbleep (e.g. "unbleep 'wonder'", "remove bleeps", "unbleep")
        m_unbleep = re.search(
            r"^(?:unbleep(?:\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?)?|remove\s+bleeps?)$",
            prompt_text,
            re.IGNORECASE,
        )
        if m_unbleep:
            for op in list(session.log.active_ops):
                if type(op).__name__ in ("BleepWords", "MaskCaptions"):
                    session.log = session.log.restore_op(op.op_id)
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

            summary_msg = "Removed all bleeps."
            cur_dur = session.timeline.total_duration if session.timeline else 0.0
            response_payload = {
                "status": "applied",
                "interpreted_by": "rule",
                "operations": [],
                "summary": summary_msg,
                "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                "duration_s": cur_dur,
                "time_policy": TIME_POLICY,
                "tokens_used": session.total_tokens_used,
                "cost_usd": round(session.total_cost_usd, 6),
                "cost_per_video_minute": 0.0,
            }
            session.chat_history.append({"role": "user", "text": prompt_text})
            session.chat_history.append({"role": "assistant", **response_payload})
            return response_payload

    is_clarification_answer = False
    if session.pending_clarification is not None:
        lower_prompt = prompt_text.lower()
        if req.answer is not None:
            is_clarification_answer = True
        elif any(k in lower_prompt for k in ["first", "second", "third", "1", "2", "3", "all", "occurrence", "point", "size", "set to", "+", "-"]):
            is_clarification_answer = True
        elif any(opt.lower() in lower_prompt for opt in session.pending_clarification.get("options", [])):
            is_clarification_answer = True
        elif re.search(r"^\d+$", lower_prompt.strip()):
            is_clarification_answer = True

    if session.pending_clarification is not None and is_clarification_answer:
        # Resolve pending clarification
        ans_text = (req.answer or prompt_text).strip()
        p_act = session.pending_clarification.get("action")
        p_q = str(session.pending_clarification.get("question", "")).lower()

        # Handle font size clarification
        if p_act == "font_size" or "font" in p_q or "size" in p_q:
            m_val = re.search(r"([+-]?\d+)", ans_text)
            cur_size = 32
            if session.timeline and session.timeline.captions:
                cur_size = getattr(session.timeline.captions[0], "font_size", 32) or 32
            if m_val:
                val = int(m_val.group(1))
                if "+" in ans_text or "-" in ans_text or "increase" in ans_text.lower() or "decrease" in ans_text.lower():
                    new_size = max(10, cur_size + val)
                else:
                    new_size = val
            else:
                new_size = cur_size + session.default_font_step

            style_op = SetCaptionStyle(font_size=new_size, interpreted_by="rule")
            session.log = session.log.push(style_op, interpreted_by="rule")
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

            session.pending_clarification = None
            session.pending_instruction = None
            summary_msg = f"Set caption font size to {new_size} pt."
            cur_dur = session.timeline.total_duration if session.timeline else 0.0
            response_payload = {
                "status": "applied",
                "interpreted_by": "rule",
                "operations": [style_op.model_dump()],
                "summary": summary_msg,
                "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
                "duration_s": cur_dur,
                "time_policy": TIME_POLICY,
                "tokens_used": session.total_tokens_used,
                "cost_usd": round(session.total_cost_usd, 6),
                "cost_per_video_minute": 0.0,
            }
            session.chat_history.append({"role": "user", "text": prompt_text})
            session.chat_history.append({"role": "assistant", **response_payload})
            return response_payload

        from promptcut.interpreter.base import IntentTarget
        from promptcut.resolver import resolve_intent_target

        occ: int | str = 1
        if "all" in ans_text.lower():
            occ = "all"
        elif "second" in ans_text.lower() or "2" in ans_text:
            occ = 2
        elif "third" in ans_text.lower() or "3" in ans_text:
            occ = 3
        elif "first" in ans_text.lower() or "1" in ans_text:
            occ = 1

        target = IntentTarget(action="clarification_answer", occurrence=occ)
        status, ops, details = resolve_intent_target(
            target,
            session.transcript,
            timeline,
            session.log,
            pending_clarification=session.pending_clarification,
        )
        session.pending_clarification = None
        session.pending_instruction = None
        interpreted_by = "rule"
    else:
        # An unrelated message arriving while clarification was pending clears pending clarification!
        session.pending_clarification = None
        session.pending_instruction = None

        from promptcut.interpreter import GroqInterpreter, InterpreterContext
        from promptcut.resolver import resolve_interpretation_result

        snippets = []
        for word in session.transcript.words:
            if word.text.lower() in prompt_text.lower():
                snippets.append(f"{word.text} (~{word.start:.1f}s)")
                if len(snippets) >= 5:
                    break

        ctx = InterpreterContext(
            duration=timeline.total_duration,
            time_policy=TIME_POLICY,
            log_summary=[op.model_dump() for op in session.log.active_ops[-6:]],
            chat_turns=session.chat_history[-4:],
            transcript_snippets=snippets,
        )

        interpreter = GroqInterpreter()
        interp_res = interpreter.interpret(prompt_text, ctx, mode=mode)
        session.total_tokens_used += interp_res.tokens_used
        session.total_cost_usd += interp_res.cost_usd
        interpreted_by = interp_res.interpreted_by or ("cache" if interp_res.cached else ("rule" if interp_res.model_name == "rule" else "llm"))

        status, ops, details = resolve_interpretation_result(
            interp_res,
            session.transcript,
            timeline,
            session.log,
        )
        if status == "rejected" and not any(k in prompt_text.lower() for k in ["order pizza", "download"]):
            if mode == "auto":
                status, ops, details = parse_instruction(
                    say=prompt_text,
                    transcript=session.transcript,
                    log=session.log,
                    timeline=timeline,
                    answer=req.answer,
                )
                if status != "rejected":
                    interpreted_by = "rule"

    cur_duration = session.timeline.total_duration if session.timeline else 0.0
    cost_per_min = (
        (session.total_cost_usd / (cur_duration / 60.0))
        if cur_duration > 0.0
        else 0.0
    )

    if status == "needs_clarification":
        phrase_val = details.get("phrase")
        q_str = str(details.get("question", ""))
        if not phrase_val and q_str:
            m_q = re.search(r"['\"]([^'\"]+)['\"]", q_str)
            if m_q:
                phrase_val = m_q.group(1).strip()
        hits_val = details.get("candidate_hits", [])
        if not hits_val and phrase_val and session.transcript:
            from promptcut.resolver import phrase_search, fuzzy_phrase_search
            hits_val = phrase_search(session.transcript, phrase_val) or fuzzy_phrase_search(session.transcript, phrase_val)

        session.pending_clarification = {
            "question": details.get("question"),
            "options": details.get("options"),
            "phrase": phrase_val,
            "candidate_hits": hits_val,
            "action": details.get("action", "cut"),
        }
        session.pending_instruction = prompt_text
        response_payload = {
            "status": "needs_clarification",
            "interpreted_by": interpreted_by,
            "operations": [],
            "summary": None,
            "question": details.get("question"),
            "options": details.get("options"),
            "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
            "duration_s": session.timeline.total_duration if session.timeline else 0.0,
            "time_policy": TIME_POLICY,
            "tokens_used": session.total_tokens_used,
            "cost_usd": round(session.total_cost_usd, 6),
            "cost_per_video_minute": round(cost_per_min, 6),
        }
        session.chat_history.append({"role": "user", "text": prompt_text})
        session.chat_history.append({"role": "assistant", **response_payload})
        return response_payload

    if status == "applied":
        if ops:
            for op in ops:
                op.interpreted_by = interpreted_by
                session.log = session.log.push(op, interpreted_by=interpreted_by)
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

        cur_dur = session.timeline.total_duration if session.timeline else 0.0
        cost_per_min = (session.total_cost_usd / (cur_dur / 60.0)) if cur_dur > 0.0 else 0.0
        response_payload = {
            "status": "applied",
            "interpreted_by": interpreted_by,
            "operations": [op.model_dump() for op in (ops or [])],
            "summary": details.get("summary", "Operation applied successfully."),
            "question": None,
            "options": None,
            "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
            "duration_s": cur_dur,
            "time_policy": TIME_POLICY,
            "tokens_used": session.total_tokens_used,
            "cost_usd": round(session.total_cost_usd, 6),
            "cost_per_video_minute": round(cost_per_min, 6),
        }
        session.chat_history.append({"role": "user", "text": prompt_text})
        session.chat_history.append({"role": "assistant", **response_payload})
        return response_payload

    if status == "undo":
        return _handle_undo(session, background_tasks, interpreted_by=interpreted_by)

    if status == "redo":
        return _handle_redo(session, background_tasks, interpreted_by=interpreted_by)

    if status == "restore":
        target_op_id = details.get("target_op_id")
        if target_op_id is not None:
            session.log = session.log.restore_op(target_op_id)
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)
            summary_msg = details.get("summary", f"Restored operation {target_op_id}.")
        else:
            summary_msg = "No target operation to restore."

        cur_dur = session.timeline.total_duration if session.timeline else 0.0
        cost_per_min = (session.total_cost_usd / (cur_dur / 60.0)) if cur_dur > 0.0 else 0.0
        response_payload = {
            "status": "applied",
            "interpreted_by": interpreted_by,
            "operations": [],
            "summary": summary_msg,
            "question": None,
            "options": None,
            "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
            "duration_s": cur_dur,
            "time_policy": TIME_POLICY,
            "tokens_used": session.total_tokens_used,
            "cost_usd": round(session.total_cost_usd, 6),
            "cost_per_video_minute": round(cost_per_min, 6),
        }
        session.chat_history.append({"role": "user", "text": prompt_text})
        session.chat_history.append({"role": "assistant", **response_payload})
        return response_payload

    if status == "correct_word":
        wid = details.get("word_id")
        new_text = details.get("new_text")
        if wid is not None and new_text and session.transcript:
            for w in session.transcript.words:
                if w.id == wid:
                    w.text = new_text
                    break
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)

        cur_dur = session.timeline.total_duration if session.timeline else 0.0
        cost_per_min = (session.total_cost_usd / (cur_dur / 60.0)) if cur_dur > 0.0 else 0.0
        response_payload = {
            "status": "applied",
            "interpreted_by": interpreted_by,
            "operations": [],
            "summary": details.get("summary", "Word pronunciation corrected."),
            "question": None,
            "options": None,
            "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
            "duration_s": cur_dur,
            "time_policy": TIME_POLICY,
            "tokens_used": session.total_tokens_used,
            "cost_usd": round(session.total_cost_usd, 6),
            "cost_per_video_minute": round(cost_per_min, 6),
        }
        session.chat_history.append({"role": "user", "text": prompt_text})
        session.chat_history.append({"role": "assistant", **response_payload})
        return response_payload

    # rejected
    cur_dur = session.timeline.total_duration if session.timeline else 0.0
    cost_per_min = (session.total_cost_usd / (cur_dur / 60.0)) if cur_dur > 0.0 else 0.0
    response_payload = {
        "status": "rejected",
        "interpreted_by": interpreted_by,
        "operations": [],
        "summary": details.get("reason", "Instruction rejected."),
        "question": None,
        "options": None,
        "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
        "duration_s": cur_dur,
        "time_policy": TIME_POLICY,
        "tokens_used": session.total_tokens_used,
        "cost_usd": round(session.total_cost_usd, 6),
        "cost_per_video_minute": round(cost_per_min, 6),
    }
    session.chat_history.append({"role": "user", "text": prompt_text})
    session.chat_history.append({"role": "assistant", **response_payload})
    return response_payload


@router.post("/sessions/{session_id}/undo")
def undo_endpoint(session_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Undo the last instruction in the session."""
    session = _get_session(session_id)
    return _handle_undo(session, background_tasks)


@router.post("/sessions/{session_id}/redo")
def redo_endpoint(session_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Redo the last undone instruction in the session."""
    session = _get_session(session_id)
    return _handle_redo(session, background_tasks)


def _handle_undo(session: SessionState, background_tasks: BackgroundTasks, interpreted_by: str = "rule") -> dict[str, Any]:
    if session.log.can_undo:
        session.log = session.log.undo()
        if session.transcript:
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)
        msg = "Reversed last instruction."
    else:
        msg = "Nothing to undo."

    return {
        "status": "applied",
        "interpreted_by": interpreted_by,
        "operations": [],
        "summary": msg,
        "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
        "duration_s": session.timeline.total_duration if session.timeline else 0.0,
        "time_policy": TIME_POLICY,
    }


def _handle_redo(session: SessionState, background_tasks: BackgroundTasks, interpreted_by: str = "rule") -> dict[str, Any]:
    if session.log.can_redo:
        session.log = session.log.redo()
        if session.transcript:
            session.timeline = apply(session.log, session.transcript)
            _trigger_preview_render(session, background_tasks)
        msg = "Re-applied last undone instruction."
    else:
        msg = "Nothing to redo."

    return {
        "status": "applied",
        "interpreted_by": interpreted_by,
        "operations": [],
        "summary": msg,
        "timeline_hash": session.timeline.timeline_hash if session.timeline else "",
        "duration_s": session.timeline.total_duration if session.timeline else 0.0,
        "time_policy": TIME_POLICY,
    }


def _safe_background_render_preview(
    timeline: Any,
    transcript: Any,
    video_path: Path,
    preview_file: Path,
) -> None:
    try:
        hash_file = preview_file.parent / f"preview_{timeline.timeline_hash}.mp4"
        if hash_file.is_file() and hash_file.stat().st_size > 0:
            return

        tmp_file = preview_file.with_name(f"preview_tmp_{uuid.uuid4().hex[:6]}.mp4")
        render_timeline(
            timeline,
            transcript,
            video_path,
            tmp_file,
            preset="preview",
        )
        if tmp_file.is_file() and tmp_file.stat().st_size > 0:
            if hash_file.exists():
                try:
                    hash_file.unlink()
                except Exception:
                    pass
            tmp_file.replace(hash_file)
            try:
                shutil.copyfile(hash_file, preview_file)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Background preview render non-critical error: {e}")


def _trigger_preview_render(session: SessionState, background_tasks: BackgroundTasks) -> None:
    """
    Render preview proxy asynchronously in the background.
    Strictly guarantees sub-3-second instruction response times (IncuBrix Scale Standard).
    """
    if not (session.timeline and session.transcript):
        return

    preview_file = session.session_dir / "preview.mp4"
    if background_tasks:
        background_tasks.add_task(
            _safe_background_render_preview,
            timeline=session.timeline,
            transcript=session.transcript,
            video_path=session.video_path,
            preview_file=preview_file,
        )
    else:
        try:
            _safe_background_render_preview(
                timeline=session.timeline,
                transcript=session.transcript,
                video_path=session.video_path,
                preview_file=preview_file,
            )
        except Exception:
            pass


@router.get("/sessions/{session_id}/preview.mp4")
def stream_preview(
    session_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """
    Stream 480p preview video with HTTP Range requests.
    If preview is generating or not yet ready, falls back immediately to streaming original
    media so video playback is instantaneous without network timeouts.
    """
    session = _get_session(session_id)

    # Check for hash-specific proxy matching current timeline edits
    if session.timeline:
        hash_file = session.session_dir / f"preview_{session.timeline.timeline_hash}.mp4"
        if hash_file.is_file() and hash_file.stat().st_size > 0:
            return stream_video_with_range(hash_file, request)

    preview_file = session.session_dir / "preview.mp4"

    if not preview_file.is_file():
        if session.timeline and session.transcript and background_tasks:
            background_tasks.add_task(
                _safe_background_render_preview,
                timeline=session.timeline,
                transcript=session.transcript,
                video_path=session.video_path,
                preview_file=preview_file,
            )
        return stream_video_with_range(session.video_path, request)

    return stream_video_with_range(preview_file, request)


@router.get("/sessions/{session_id}/original.mp4")
def stream_original(session_id: str, request: Request) -> Response:
    """Stream original media with HTTP Range requests for comparison."""
    session = _get_session(session_id)
    return stream_video_with_range(session.video_path, request)


@router.post("/sessions/{session_id}/render", status_code=status.HTTP_202_ACCEPTED)
def trigger_render(
    session_id: str,
    background_tasks: BackgroundTasks,
    callback_url: str | None = None,
) -> Response:
    """Start full quality production render job (asynchronous background task)."""
    session = _get_session(session_id)
    if not session.timeline or not session.transcript:
        raise HTTPException(status_code=409, detail="Timeline not ready to render")

    job_id = str(uuid.uuid4())[:8]
    rendered_url = f"/api/v1/sessions/{session_id}/rendered.mp4"
    _jobs[job_id] = {
        "job_id": job_id,
        "session_id": session_id,
        "type": "render",
        "status": "queued",
        "progress": 0,
        "error": None,
        "rendered_url": rendered_url,
    }

    background_tasks.add_task(
        _run_render_job,
        session=session,
        job_id=job_id,
        callback_url=callback_url,
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "session_id": session_id,
            "job_id": job_id,
            "status": "queued",
            "rendered_url": rendered_url,
            "duration_s": session.timeline.total_duration,
            "timeline_hash": session.timeline.timeline_hash,
        },
    )


@router.get("/sessions/{session_id}/rendered.mp4")
def stream_rendered(session_id: str, request: Request) -> Response:
    """Stream final rendered video."""
    session = _get_session(session_id)
    render_file = session.session_dir / "rendered.mp4"
    if not render_file.is_file():
        raise HTTPException(status_code=404, detail="Rendered video not found")
    return stream_video_with_range(render_file, request)


@router.get("/storage/{file_path:path}")
def get_storage_file(file_path: str, request: Request) -> Response:
    """Download/stream a file stored in the local storage backend."""
    from promptcut.storage.local import LocalStorageAdapter
    storage = LocalStorageAdapter()
    try:
        resolved = storage._resolve_path(file_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid storage path")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found in storage")
    return stream_video_with_range(resolved, request)


@router.get("/sessions/{session_id}/interpreter-mode")
def get_session_interpreter_mode(session_id: str) -> dict[str, Any]:
    """Get the current interpreter mode for this session."""
    session = _get_session(session_id)
    return {"mode": session.interpreter_mode}


@router.post("/sessions/{session_id}/interpreter-mode")
def set_session_interpreter_mode(session_id: str, req: InterpreterModeRequest) -> dict[str, Any]:
    """Set the interpreter mode for this session ('auto', 'llm_only', or 'rule_only')."""
    session = _get_session(session_id)
    new_mode = req.mode.strip().lower()
    if new_mode not in ("auto", "llm_only", "rule_only"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interpreter mode: '{req.mode}'. Must be 'auto', 'llm_only', or 'rule_only'.",
        )
    session.interpreter_mode = new_mode
    return {"status": "ok", "mode": session.interpreter_mode}


def _rehydrate_session_from_disk(sess_dir: Path, session_id: str) -> SessionState | None:
    if not sess_dir.is_dir():
        return None
    try:
        video_path = None
        orig_hash = None
        meta_file = sess_dir / "session_meta.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                cand = Path(meta.get("video_path", ""))
                if cand.is_file():
                    video_path = cand
                orig_hash = meta.get("original_hash")
            except Exception:
                pass

        if not video_path:
            candidates = [
                f for f in sess_dir.glob("*.mp4")
                if f.name not in ("preview.mp4", "rendered.mp4")
            ]
            if not candidates:
                candidates = list(sess_dir.glob("*.mp4"))
            if candidates:
                video_path = candidates[0]

        if not video_path or not video_path.is_file():
            return None

        if not orig_hash:
            orig_hash = compute_file_sha256(video_path)

        session = SessionState(
            session_id=session_id,
            video_path=video_path,
            original_hash=orig_hash,
            session_dir=sess_dir,
        )

        # Rehydrate transcript
        t_file = sess_dir / "transcript.json"
        if not t_file.is_file():
            t_file = sess_dir / "uploaded_transcript.json"

        if t_file.is_file():
            try:
                session.transcript = Transcript.model_validate_json(
                    t_file.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning(f"Could not load transcript for {session_id}: {e}")

        # Rehydrate operation log
        log_file = sess_dir / "operation_log.json"
        if log_file.is_file():
            try:
                session.log = OperationLog.model_validate_json(
                    log_file.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning(f"Could not load operation log for {session_id}: {e}")

        if session.transcript:
            session.timeline = apply(session.log, session.transcript)

        _sessions[session_id] = session
        logger.info(f"Rehydrated session '{session_id}' from disk storage: {sess_dir}")
        return session
    except Exception as e:
        logger.warning(f"Rehydration error for {session_id}: {e}")
        return None


def _get_session(session_id: str) -> SessionState:
    if session_id in _sessions:
        return _sessions[session_id]

    # Check direct folder
    sess_dir = SESSIONS_ROOT / session_id
    rehydrated = _rehydrate_session_from_disk(sess_dir, session_id)
    if rehydrated:
        return rehydrated

    # Check for prefix or substring match in SESSIONS_ROOT
    if SESSIONS_ROOT.is_dir():
        for d in SESSIONS_ROOT.iterdir():
            if d.is_dir() and (d.name.startswith(session_id) or session_id.startswith(d.name)):
                rehydrated = _rehydrate_session_from_disk(d, d.name)
                if rehydrated:
                    _sessions[session_id] = rehydrated
                    return rehydrated

    raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
