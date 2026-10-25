"""
Session Runner for Scripted Multi-Step Edit Sessions.

Executes sequential natural language instructions from a YAML session file,
tracks OperationLog and Timeline state, manages clarification loops,
burns in captions and renders video via FFmpeg, and emits results.json,
timeline.json, and the final rendered media while verifying source immutability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import uuid

from ruamel.yaml import YAML

from promptcut.config import get_interpreter_mode
from promptcut.diarization.sidecar import SidecarDiarizationAdapter
from promptcut.engine import apply
from promptcut.media.intake import (
    check_original_unchanged,
    compute_file_sha256,
    inspect_media,
)
from promptcut.models.operation_log import OperationLog
from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript
from promptcut.otio_export import export_otio
from promptcut.render.ffmpeg import render_timeline
from promptcut.session.parser import parse_instruction
from promptcut.transcription.mock import MockTranscriptionAdapter
from promptcut.transcription.whisper import FasterWhisperAdapter


def run_session(
    session_file: str | Path,
    output_dir: str | Path,
    *,
    render_preset: str = "final",
) -> dict[str, Any]:
    """
    Execute a scripted edit session from a YAML session definition.

    Parameters
    ----------
    session_file : str | Path
        Path to the YAML session file.
    output_dir : str | Path
        Directory where results.json, timeline.json, timeline.otio,
        and rendered video will be saved.
    render_preset : str
        FFmpeg rendering preset ("preview" or "final"). Default is "final".

    Returns
    -------
    dict[str, Any]
        Session execution summary dictionary containing step results,
        timeline hash, final duration, and output paths.
    """
    session_path = Path(session_file).resolve()
    if not session_path.is_file():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Parse YAML session file
    yaml = YAML(typ="safe")
    with open(session_path, "r", encoding="utf-8") as f:
        spec = yaml.load(f)

    if not isinstance(spec, dict):
        raise ValueError(f"Invalid session file format: expected a YAML dictionary in {session_path}")

    # 2. Resolve media file
    video_raw = spec.get("video")
    if not video_raw:
        raise ValueError("Session definition must contain a 'video' path entry.")

    video_path = Path(video_raw)
    if not video_path.is_absolute():
        candidate_rel_session = session_path.parent / video_path
        if candidate_rel_session.is_file():
            video_path = candidate_rel_session.resolve()
        else:
            video_path = video_path.resolve()

    if not video_path.is_file():
        raise FileNotFoundError(f"Source video not found: {video_path}")

    # 3. Media Intake & Immutability Check
    media_info = inspect_media(video_path)
    original_hash = compute_file_sha256(video_path)

    import time
    start_time = time.time()
    session_id = spec.get("session_id") or uuid.uuid4().hex[:8]

    # 4. Resolve Transcript (Mock fixture vs Faster-Whisper)
    transcription_provider = "mock"
    transcript_fixture = spec.get("transcript") or spec.get("transcript_fixture")
    if transcript_fixture:
        fixture_path = Path(transcript_fixture)
        if not fixture_path.is_absolute():
            candidate = session_path.parent / fixture_path
            if candidate.is_file():
                fixture_path = candidate.resolve()
            else:
                fixture_path = fixture_path.resolve()
        adapter = MockTranscriptionAdapter(fixture_path)
        transcript = adapter.transcribe(video_path)
        transcription_provider = "mock"
    else:
        model_name = spec.get("model_name")
        device = spec.get("device")
        cache_dir = spec.get("cache_dir")
        whisper_kwargs: dict[str, Any] = {}
        if model_name:
            whisper_kwargs["model_name"] = model_name
        if device:
            whisper_kwargs["device"] = device
        if cache_dir:
            whisper_kwargs["cache_dir"] = cache_dir

        whisper_adapter = FasterWhisperAdapter(**whisper_kwargs)
        try:
            transcript = whisper_adapter.transcribe(video_path, source_hash=original_hash)
            transcription_provider = "faster-whisper"
        except (ImportError, Exception):
            try:
                from promptcut.transcription.vosk_adapter import VoskTranscriptionAdapter
                vosk_adapter = VoskTranscriptionAdapter()
                transcript = vosk_adapter.transcribe(video_path, source_hash=original_hash)
                transcription_provider = "vosk"
            except Exception:
                mock_adapter = MockTranscriptionAdapter()
                transcript = mock_adapter.transcribe(video_path, source_hash=original_hash)
                transcription_provider = "mock"

    # 5. Optional Speaker Sidecar
    sidecar_raw = spec.get("sidecar") or spec.get("diarization_sidecar")
    if sidecar_raw:
        sidecar_path = Path(sidecar_raw)
        if not sidecar_path.is_absolute():
            candidate = session_path.parent / sidecar_path
            if candidate.is_file():
                sidecar_path = candidate.resolve()
            else:
                sidecar_path = sidecar_path.resolve()
        diar_adapter = SidecarDiarizationAdapter()
        transcript = diar_adapter.apply_speakers(transcript, sidecar_path)

    # 6. Execute Steps sequentially
    steps = spec.get("steps") or spec.get("instructions") or []
    log = OperationLog()
    timeline = apply(log, transcript)

    step_results: list[dict[str, Any]] = []
    pending_say: str | None = None
    has_rendered = False
    output_video_name = spec.get("output_video", "rendered.mp4")
    render_out_path = out_dir / output_video_name

    for idx, step_dict in enumerate(steps, start=1):
        if not isinstance(step_dict, dict):
            continue

        raw_say = step_dict.get("say") or step_dict.get("answer") or ""

        mode = get_interpreter_mode()
        interpreted_by = "rule"

        if mode == "llm_only":
            from promptcut.interpreter import GroqInterpreter, InterpreterContext
            from promptcut.resolver import resolve_interpretation_result

            ctx = InterpreterContext(
                duration=timeline.total_duration,
                time_policy="current",
                log_summary=[op.model_dump() for op in log.active_ops[-6:]],
                transcript_snippets=[w.text for w in transcript.words[:10]],
            )
            interp = GroqInterpreter()
            res = interp.interpret(raw_say, ctx)
            interpreted_by = res.interpreted_by or ("cache" if res.cached else "llm")
            status, ops, details = resolve_interpretation_result(res, transcript, timeline, log)
        else:
            if pending_say is not None:
                # Check if raw_say is an answer or an unrelated new instruction
                is_answer = bool(step_dict.get("answer")) or any(
                    k in raw_say.lower() for k in ["first", "second", "third", "1", "2", "3", "all", "occurrence"]
                )
                if is_answer:
                    status, ops, details = parse_instruction(
                        say=pending_say,
                        transcript=transcript,
                        log=log,
                        timeline=timeline,
                        answer=raw_say,
                    )
                    pending_say = None
                else:
                    # Unrelated message arrived: clear pending clarification and process new instruction
                    pending_say = None
                    status, ops, details = parse_instruction(
                        say=raw_say,
                        transcript=transcript,
                        log=log,
                        timeline=timeline,
                    )
            else:
                status, ops, details = parse_instruction(
                    say=raw_say,
                    transcript=transcript,
                    log=log,
                    timeline=timeline,
                )
            interpreted_by = details.get("interpreted_by", "rule")

        if status == "needs_clarification":
            pending_say = raw_say
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "needs_clarification",
                "interpreted_by": interpreted_by,
                "operations": [],
                "summary": None,
                "question": details.get("question"),
                "options": details.get("options"),
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        if status == "applied":
            if ops:
                for op in ops:
                    op.interpreted_by = interpreted_by
                    log = log.push(op, interpreted_by=interpreted_by)
                timeline = apply(log, transcript)
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "applied",
                "interpreted_by": interpreted_by,
                "operations": [op.model_dump() for op in (ops or [])],
                "summary": details.get("summary"),
                "question": None,
                "options": None,
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        if status == "undo":
            if log.can_undo:
                log = log.undo()
                timeline = apply(log, transcript)
                summary = "Reversed last instruction."
            else:
                summary = "Nothing to undo."
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "applied",
                "interpreted_by": interpreted_by,
                "operations": [],
                "summary": summary,
                "question": None,
                "options": None,
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        if status == "redo":
            if log.can_redo:
                log = log.redo()
                timeline = apply(log, transcript)
                summary = "Re-applied last undone instruction."
            else:
                summary = "Nothing to redo."
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "applied",
                "interpreted_by": interpreted_by,
                "operations": [],
                "summary": summary,
                "question": None,
                "options": None,
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        if status == "restore":
            target_op_id = details.get("target_op_id")
            if target_op_id is not None:
                log = log.restore_op(target_op_id)
                timeline = apply(log, transcript)
                summary = details.get("summary", f"Restored operation {target_op_id}.")
            else:
                summary = "No target operation found to restore."
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "applied",
                "interpreted_by": interpreted_by,
                "operations": [],
                "summary": summary,
                "question": None,
                "options": None,
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        if status == "correct_word":
            wid = details.get("word_id")
            new_text = details.get("new_text")
            if wid is not None and new_text:
                for w in transcript.words:
                    if w.id == wid:
                        w.text = new_text
                        break
                timeline = apply(log, transcript)
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "applied",
                "interpreted_by": interpreted_by,
                "operations": [],
                "summary": details.get("summary"),
                "question": None,
                "options": None,
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        if status == "render":
            render_timeline(
                timeline,
                transcript,
                video_path,
                render_out_path,
                preset=render_preset,  # type: ignore
            )
            has_rendered = True
            step_results.append({
                "step": idx,
                "say": raw_say,
                "status": "rendered",
                "interpreted_by": interpreted_by,
                "operations": [],
                "summary": f"Rendered output to {render_out_path.name}",
                "question": None,
                "options": None,
                "timeline_hash": timeline.timeline_hash,
                "duration_s": timeline.total_duration,
            })
            continue

        # rejected / unknown
        step_results.append({
            "step": idx,
            "say": raw_say,
            "status": "rejected",
            "interpreted_by": interpreted_by,
            "operations": [],
            "summary": details.get("reason", "Instruction rejected."),
            "question": None,
            "options": None,
            "timeline_hash": timeline.timeline_hash,
            "duration_s": timeline.total_duration,
        })

    # 7. Write results.json and timeline.json
    results_path = out_dir / "results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(step_results, f, indent=2)

    timeline_path = out_dir / "timeline.json"
    with open(timeline_path, "w", encoding="utf-8") as f:
        f.write(timeline.model_dump_json(indent=2))

    otio_path = out_dir / "timeline.otio"
    export_otio(timeline, transcript, otio_path, project_name=video_path.stem)

    # IncuBrix Integration Standard #6: Write manifest.json
    from promptcut.models.manifest import build_job_manifest, write_manifest_file
    manifest = build_job_manifest(
        job_id=session_id,
        session_id=session_id,
        job_type="session",
        transcription_provider=transcription_provider,
        seed=spec.get("seed"),
        duration_ms=(time.time() - start_time) * 1000.0,
    )
    manifest_path = out_dir / "manifest.json"
    write_manifest_file(manifest, manifest_path)

    # 8. Verify Source Immutability (Must-Have 1)
    check_original_unchanged(video_path, original_hash)

    return {
        "status": "success",
        "steps_count": len(step_results),
        "results_json": str(results_path),
        "timeline_json": str(timeline_path),
        "timeline_otio": str(otio_path),
        "manifest_json": str(manifest_path),
        "rendered_video": str(render_out_path) if has_rendered else None,
        "timeline_hash": timeline.timeline_hash,
        "total_duration": timeline.total_duration,
        "cost_per_video_minute": 0.0,
        "source_verified_unchanged": True,
    }
