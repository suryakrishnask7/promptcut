"""
Command Line Interface (CLI) for promptcut.

Provides:
- promptcut transcribe <video.mp4> -o <transcript.json> [--adapter mock|faster-whisper] [--sidecar speakers.json]
- promptcut inspect <transcript.json>
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from promptcut.diarization import MockSidecarDiarizationAdapter
from promptcut.models.transcript import Transcript
from promptcut.transcription import FasterWhisperAdapter, MockTranscriptionAdapter


def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS.mmm."""
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptcut",
        description="PromptCut – Prompt-Based Video Editing Engine CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: transcribe
    p_transcribe = subparsers.add_parser(
        "transcribe",
        help="Transcribe an audio/video file and output a promptcut Transcript JSON",
    )
    p_transcribe.add_argument("media", help="Path to input audio/video file")
    p_transcribe.add_argument(
        "-o", "--output", default="transcript.json", help="Output JSON path"
    )
    p_transcribe.add_argument(
        "--adapter",
        choices=["mock", "faster-whisper"],
        default="mock",
        help="Transcription provider (default: mock)",
    )
    p_transcribe.add_argument(
        "--sidecar",
        default=None,
        help="Optional path to speaker diarization sidecar JSON",
    )
    p_transcribe.add_argument(
        "--language", default="en", help="Audio language code (default: en)"
    )

    # Subcommand: inspect
    p_inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a Transcript JSON file and view word IDs, times, and speakers",
    )
    p_inspect.add_argument("transcript", help="Path to Transcript JSON file")

    # Subcommand: run-session
    p_session = subparsers.add_parser(
        "run-session",
        help="Run a scripted multi-step editing session from a YAML session file",
    )
    p_session.add_argument("session_file", help="Path to YAML session file")
    p_session.add_argument(
        "-o",
        "--output-dir",
        default="out",
        help="Directory to save results.json, timeline.json, and rendered video (default: out)",
    )
    p_session.add_argument(
        "--preset",
        choices=["preview", "final"],
        default="final",
        help="FFmpeg render preset (default: final)",
    )

    # Subcommand: serve
    p_serve = subparsers.add_parser(
        "serve",
        help="Launch the local FastAPI server and web UI",
    )
    p_serve.add_argument(
        "--host",
        default=os.getenv("HOST", "127.0.0.1"),
        help="Host interface (default: 127.0.0.1 or $HOST)",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8000")),
        help="Port (default: 8000 or $PORT)",
    )
    p_serve.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("UVICORN_RELOAD", "false").lower() in ("true", "1"),
        help="Enable uvicorn auto-reload (default: false or $UVICORN_RELOAD)",
    )

    # Subcommand: batch
    p_batch = subparsers.add_parser(
        "batch",
        help="Run every YAML session in a folder, record per-session results and summary.json",
    )
    p_batch.add_argument("folder", help="Directory containing YAML session files")
    p_batch.add_argument(
        "-o", "--output", default="batch_out", help="Directory to save per-session outputs and summary.json"
    )
    p_batch.add_argument(
        "--preset",
        choices=["preview", "final"],
        default="final",
        help="FFmpeg render preset (default: final)",
    )
    p_batch.add_argument(
        "--api",
        action="store_true",
        help="Execute batch test cases against the PromptCut HTTP API",
    )
    p_batch.add_argument(
        "--api-url",
        default=os.getenv("PROMPTCUT_API_URL", "http://localhost:8000"),
        help="PromptCut API base URL when running in --api mode (default: http://localhost:8000)",
    )

    return parser


def handle_transcribe(args: argparse.Namespace) -> int:
    media_path = Path(args.media)
    if not media_path.exists():
        print(f"Error: media file not found: {media_path}", file=sys.stderr)
        return 1

    if args.adapter == "faster-whisper":
        adapter = FasterWhisperAdapter()
    else:
        adapter = MockTranscriptionAdapter()

    print(f"Transcribing '{media_path}' using adapter '{adapter.model_name}'...")
    transcript = adapter.transcribe(media_path, language=args.language)

    if args.sidecar:
        sidecar_path = Path(args.sidecar)
        print(f"Applying speaker sidecar from '{sidecar_path}'...")
        diarizer = MockSidecarDiarizationAdapter()
        transcript = diarizer.apply_speakers(transcript, sidecar_path)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"Wrote transcript to '{out_path}' ({len(transcript.words)} words, "
        f"{transcript.source_duration:.2f}s)"
    )
    return 0


def handle_inspect(args: argparse.Namespace) -> int:
    t_path = Path(args.transcript)
    if not t_path.exists():
        print(f"Error: transcript file not found: {t_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(t_path.read_text(encoding="utf-8"))
        transcript = Transcript.model_validate(data)
    except Exception as e:
        print(f"Error: failed to parse Transcript JSON: {e}", file=sys.stderr)
        return 1

    print("=" * 70)
    print(f"Transcript: {transcript.title or t_path.name}")
    print(f"Total Words: {len(transcript.words)} | Duration: {format_timestamp(transcript.source_duration)}")
    print("=" * 70)
    print(f"{'ID':>5} | {'START':>10} -> {'END':>10} | {'SPK':>4} | {'CONF':>5} | WORD")
    print("-" * 70)

    for w in transcript.words:
        s_str = format_timestamp(w.start)
        e_str = format_timestamp(w.end)
        conf_str = f"{w.confidence:.2f}"
        print(f"[{w.id:04d}] | {s_str} -> {e_str} | {w.speaker:>4} | {conf_str:>5} | {w.text}")

    print("=" * 70)
    return 0


def handle_run_session(args: argparse.Namespace) -> int:
    from promptcut.session.runner import run_session

    session_path = Path(args.session_file)
    if not session_path.exists():
        print(f"Error: session file not found: {session_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir)
    print(f"Executing session '{session_path}' -> output to '{out_dir}' (preset: {args.preset})...")

    try:
        summary = run_session(
            session_file=session_path,
            output_dir=out_dir,
            render_preset=args.preset,
        )
    except Exception as e:
        print(f"Error executing session: {e}", file=sys.stderr)
        return 1

    print("=" * 70)
    print("SESSION EXECUTION COMPLETE")
    print(f"Steps executed: {summary['steps_count']}")
    print(f"Results JSON:   {summary['results_json']}")
    print(f"Timeline JSON:  {summary['timeline_json']}")
    print(f"Timeline OTIO:  {summary['timeline_otio']}")
    if summary.get("rendered_video"):
        print(f"Rendered Video: {summary['rendered_video']}")
    print(f"Timeline Hash:  {summary['timeline_hash']}")
    print(f"Final Duration: {summary['total_duration']:.2f}s")
    print(f"Source Intact:  {summary['source_verified_unchanged']}")
    print("=" * 70)
    return 0


def handle_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn is required to serve the web UI. Install with: pip install uvicorn", file=sys.stderr)
        return 1

    print(f"Starting PromptCut Web UI at: http://{args.host}:{args.port}/ui (reload={args.reload})")
    print("Press Ctrl+C to stop.")
    uvicorn.run("promptcut.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def handle_batch(args: argparse.Namespace) -> int:
    """Run every YAML session in a folder, write per-session results and summary.json, continuing past failures."""
    if getattr(args, "api", False):
        import importlib.util
        batch_runner_path = Path(__file__).resolve().parents[3] / "scripts" / "batch_runner.py"
        spec = importlib.util.spec_from_file_location("batch_runner", batch_runner_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.main([str(args.folder), "-o", str(args.output), "--api-url", args.api_url])

    from promptcut.session.runner import run_session

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Error: folder not found: {folder}", file=sys.stderr)
        return 1

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    yaml_files = sorted(
        list(folder.glob("*.yaml")) + list(folder.glob("*.yml"))
    )
    if not yaml_files:
        print(f"Warning: no YAML session files found in {folder}", file=sys.stderr)

    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0

    print(f"Starting batch execution of {len(yaml_files)} session(s) from '{folder}' -> '{out_dir}'...")

    for session_file in yaml_files:
        session_name = session_file.stem
        session_out = out_dir / session_name
        session_out.mkdir(parents=True, exist_ok=True)
        print(f"  --> Running session '{session_name}'...")

        try:
            summary = run_session(
                session_file=session_file,
                output_dir=session_out,
                render_preset=args.preset,
            )
            succeeded += 1
            results.append({
                "session": session_name,
                "file": str(session_file),
                "status": "succeeded",
                "steps": summary.get("steps_count", 0),
                "duration_s": summary.get("total_duration", 0.0),
                "timeline_hash": summary.get("timeline_hash"),
                "output_dir": str(session_out),
                "error": None,
            })
            print(f"  [OK] '{session_name}' succeeded ({summary.get('steps_count', 0)} steps)")
        except Exception as e:
            failed += 1
            results.append({
                "session": session_name,
                "file": str(session_file),
                "status": "failed",
                "output_dir": str(session_out),
                "error": str(e),
            })
            print(f"  [FAIL] '{session_name}' failed: {e}", file=sys.stderr)

    summary_doc = {
        "total_sessions": len(yaml_files),
        "succeeded": succeeded,
        "failed": failed,
        "sessions": results,
    }
    summary_path = out_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_doc, f, indent=2)

    print("=" * 70)
    print(f"BATCH RUN COMPLETE: {succeeded}/{len(yaml_files)} succeeded, {failed} failed")
    print(f"Summary written to: {summary_path}")
    print("=" * 70)
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "transcribe":
        return handle_transcribe(args)
    elif args.command == "inspect":
        return handle_inspect(args)
    elif args.command == "run-session":
        return handle_run_session(args)
    elif args.command == "serve":
        return handle_serve(args)
    elif args.command == "batch":
        return handle_batch(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

