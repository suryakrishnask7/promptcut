"""
tests/test_batch_cli.py

Verifies IncuBrix Integration Standard #7:
- CLI: promptcut batch <folder> -o <out>
- Runs every YAML session
- Writes per-session results plus a summary file (summary.json)
- Continues past a failing session and reports it
"""

import json
from pathlib import Path
import subprocess
import pytest

from promptcut.cli.main import main
from promptcut.media.intake import find_executable
from promptcut.models.transcript import Transcript, Word


@pytest.fixture
def batch_setup(tmp_path: Path):
    """Set up synthetic video, transcripts, and multiple YAML session files."""
    ffmpeg_bin = find_executable("ffmpeg")
    video_path = tmp_path / "media.mp4"
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4:sample_rate=44100",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-t", "4.0", str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    words = [
        Word(id=0, text="Intro", start=0.0, end=1.0),
        Word(id=1, text="middle", start=1.0, end=2.0),
        Word(id=2, text="outro", start=2.0, end=3.0),
    ]
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(Transcript(words=words).model_dump_json(), encoding="utf-8")

    batch_dir = tmp_path / "sessions_batch"
    batch_dir.mkdir()

    # Session 1: valid
    s1 = batch_dir / "01_cut_intro.yaml"
    s1.write_text(f"""
video: "{video_path.as_posix()}"
transcript: "{transcript_path.as_posix()}"
instructions:
  - say: "cut intro"
""", encoding="utf-8")

    # Session 2: invalid (points to non-existent video)
    s2 = batch_dir / "02_failing_session.yaml"
    s2.write_text(f"""
video: "{tmp_path.as_posix()}/non_existent_file.mp4"
transcript: "{transcript_path.as_posix()}"
instructions:
  - say: "cut intro"
""", encoding="utf-8")

    # Session 3: valid (proves continuation past failing session 2)
    s3 = batch_dir / "03_cut_middle.yaml"
    s3.write_text(f"""
video: "{video_path.as_posix()}"
transcript: "{transcript_path.as_posix()}"
instructions:
  - say: "cut middle"
""", encoding="utf-8")

    return batch_dir, tmp_path / "batch_out"


def test_batch_cli_execution_and_summary(batch_setup):
    """promptcut batch <folder> -o <out> executes all, continues past errors, writes summary.json."""
    batch_dir, out_dir = batch_setup

    exit_code = main(["batch", str(batch_dir), "-o", str(out_dir)])
    # Exit code indicates failures occurred (non-zero) or 0, but execution completed
    assert out_dir.is_dir()

    # Verify per-session folders
    assert (out_dir / "01_cut_intro" / "results.json").is_file()
    assert (out_dir / "01_cut_intro" / "manifest.json").is_file()
    assert (out_dir / "03_cut_middle" / "results.json").is_file()
    assert (out_dir / "03_cut_middle" / "manifest.json").is_file()

    # Verify summary.json
    summary_path = out_dir / "summary.json"
    assert summary_path.is_file(), "summary.json must exist"

    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)

    assert summary["total_sessions"] == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1

    # Check status breakdown
    statuses = {s["session"]: s["status"] for s in summary["sessions"]}
    assert statuses["01_cut_intro"] == "succeeded"
    assert statuses["02_failing_session"] == "failed"
    assert statuses["03_cut_middle"] == "succeeded"

    # Failing session has error recorded
    failing_entry = next(s for s in summary["sessions"] if s["session"] == "02_failing_session")
    assert "error" in failing_entry
    assert failing_entry["error"] is not None
