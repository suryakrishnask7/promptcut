"""
tests/test_session_runner.py

Unit and integration tests for the Session Runner.
Tests scripted multi-step execution, clarification handling, undo/redo,
results.json, timeline.json, timeline.otio, rendered media,
source immutability verification, and CLI invocation.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import pytest
from ruamel.yaml import YAML

from promptcut.cli.main import main
from promptcut.media.intake import compute_file_sha256, find_executable, inspect_media
from promptcut.models.transcript import Transcript, Word
from promptcut.session.runner import run_session


@pytest.fixture(scope="module")
def session_media_setup(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """
    Generate an 8-second synthetic MP4 video and matching transcript fixture JSON.
    Contains:
    - "welcome to the channel" (words 0-3)
    - "pricing" twice (word 4 at ~2.5s, word 7 at ~5.0s)
    """
    tmp_dir = tmp_path_factory.mktemp("session_media")
    video_path = tmp_dir / "sample_presentation.mp4"

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
        Word(id=0, text="Welcome", start=0.0, end=0.6),
        Word(id=1, text="to", start=0.6, end=1.0),
        Word(id=2, text="the", start=1.0, end=1.4),
        Word(id=3, text="channel", start=1.4, end=2.4),
        Word(id=4, text="pricing", start=2.4, end=3.4),
        Word(id=5, text="is", start=3.4, end=4.0),
        Word(id=6, text="simple", start=4.0, end=4.8),
        Word(id=7, text="pricing", start=4.8, end=5.8),
        Word(id=8, text="tier", start=5.8, end=6.6),
        Word(id=9, text="thanks", start=6.6, end=7.8),
    ]
    transcript = Transcript(
        words=words,
        title="Session Test Presentation",
        source_duration=8.0,
    )

    transcript_path = tmp_dir / "sample_transcript.json"
    transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    return video_path, transcript_path


class TestSessionRunner:
    """Verifies end-to-end scripted session runner workflows."""

    def test_run_session_workflow(
        self, session_media_setup: tuple[Path, Path], tmp_path: Path
    ):
        video_path, transcript_path = session_media_setup
        original_hash = compute_file_sha256(video_path)

        session_yaml_path = tmp_path / "test_session.yaml"
        yaml_content = f"""
video: {video_path.as_posix()}
transcript: {transcript_path.as_posix()}
output_video: rendered_output.mp4
steps:
  - say: "add a caption 'Welcome!' when I say welcome to the channel"
  - say: "cut the first 2 seconds"
  - say: "remove the part about pricing"
  - say: "the second one"
  - say: "undo"
  - say: "redo"
  - say: "make that caption red"
  - say: "render"
"""
        session_yaml_path.write_text(yaml_content.strip(), encoding="utf-8")

        out_dir = tmp_path / "session_out"
        summary = run_session(session_yaml_path, out_dir, render_preset="preview")

        assert summary["status"] == "success"
        assert summary["steps_count"] == 8
        assert summary["source_verified_unchanged"] is True

        # Verify output files exist
        results_file = out_dir / "results.json"
        timeline_file = out_dir / "timeline.json"
        otio_file = out_dir / "timeline.otio"
        rendered_video = out_dir / "rendered_output.mp4"

        assert results_file.is_file()
        assert timeline_file.is_file()
        assert otio_file.is_file()
        assert rendered_video.is_file()

        # Check results.json contents
        results = json.loads(results_file.read_text(encoding="utf-8"))
        assert len(results) == 8

        # Step 1: Caption added
        assert results[0]["step"] == 1
        assert results[0]["status"] == "applied"
        assert len(results[0]["operations"]) == 1
        assert results[0]["operations"][0]["op_type"] == "add_caption"

        # Step 2: Cut first 2 seconds
        assert results[1]["step"] == 2
        assert results[1]["status"] == "applied"
        assert results[1]["operations"][0]["op_type"] == "cut_range"

        # Step 3: Remove pricing -> Needs clarification (2 occurrences)
        assert results[2]["step"] == 3
        assert results[2]["status"] == "needs_clarification"
        assert "pricing" in results[2]["question"].lower()
        assert len(results[2]["options"]) >= 2

        # Step 4: Clarification answered -> Applied
        assert results[3]["step"] == 4
        assert results[3]["status"] == "applied"
        assert results[3]["operations"][0]["op_type"] == "cut_range"

        # Step 5: Undo
        assert results[4]["step"] == 5
        assert results[4]["status"] == "applied"
        assert "reversed" in results[4]["summary"].lower()

        # Step 6: Redo
        assert results[5]["step"] == 6
        assert results[5]["status"] == "applied"
        assert "re-applied" in results[5]["summary"].lower()

        # Step 7: Caption color red
        assert results[6]["step"] == 7
        assert results[6]["status"] == "applied"
        assert results[6]["operations"][0]["op_type"] == "set_caption_style"
        assert results[6]["operations"][0]["color"] == "red"

        # Step 8: Render
        assert results[7]["step"] == 8
        assert results[7]["status"] == "rendered"
        assert results[7]["duration_s"] > 0

        # Verify rendered media properties with ffprobe
        info = inspect_media(rendered_video)
        assert info.has_audio is True
        assert pytest.approx(info.duration, abs=0.2) == summary["total_duration"]

        # Verify source file remained 100% unchanged
        assert compute_file_sha256(video_path) == original_hash

    def test_cli_run_session_invocation(
        self, session_media_setup: tuple[Path, Path], tmp_path: Path
    ):
        video_path, transcript_path = session_media_setup

        session_yaml = tmp_path / "cli_session.yaml"
        session_yaml.write_text(
            f"""
video: {video_path.as_posix()}
transcript: {transcript_path.as_posix()}
steps:
  - say: "cut the first 1 second"
  - say: "render"
""".strip(),
            encoding="utf-8",
        )

        out_dir = tmp_path / "cli_out"
        ret = main(["run-session", str(session_yaml), "-o", str(out_dir), "--preset", "preview"])
        assert ret == 0

        assert (out_dir / "results.json").is_file()
        assert (out_dir / "timeline.json").is_file()
        assert (out_dir / "rendered.mp4").is_file()

    def test_restore_intro_and_speed_change(
        self, session_media_setup: tuple[Path, Path], tmp_path: Path
    ):
        video_path, transcript_path = session_media_setup
        original_hash = compute_file_sha256(video_path)

        session_yaml = tmp_path / "restore_session.yaml"
        session_yaml.write_text(
            f"""
video: {video_path.as_posix()}
transcript: {transcript_path.as_posix()}
output_video: restore_rendered.mp4
steps:
  - say: "add a caption 'Welcome!' when I say welcome to the channel"
  - say: "cut the first 2 seconds"
  - say: "remove the part about pricing"
  - say: "the second one"
  - say: "speed up from 0:01 to 0:03 by 2x"
  - say: "actually, put the intro back"
  - say: "undo"
  - say: "make that caption red"
  - say: "render"
""".strip(),
            encoding="utf-8",
        )

        out_dir = tmp_path / "restore_out"
        summary = run_session(session_yaml, out_dir, render_preset="preview")

        assert summary["status"] == "success"
        assert summary["steps_count"] == 9
        assert summary["source_verified_unchanged"] is True

        results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
        # Step 6: Actually, put the intro back -> Applied restore
        assert results[5]["step"] == 6
        assert results[5]["status"] == "applied"
        assert "restored" in results[5]["summary"].lower()

        # Step 7: Undo -> reverses restore
        assert results[6]["step"] == 7
        assert results[6]["status"] == "applied"
        assert "reversed" in results[6]["summary"].lower()

        # Rendered file check
        rendered_file = out_dir / "restore_rendered.mp4"
        assert rendered_file.is_file()
        info = inspect_media(rendered_file)
        assert pytest.approx(info.duration, abs=0.2) == summary["total_duration"]
        assert compute_file_sha256(video_path) == original_hash

    def test_session_fillers_silence_and_rejections(
        self, session_media_setup: tuple[Path, Path], tmp_path: Path
    ):
        video_path, transcript_path = session_media_setup
        session_yaml = tmp_path / "fillers_session.yaml"
        session_yaml.write_text(
            f"""
video: {video_path.as_posix()}
transcript: {transcript_path.as_posix()}
steps:
  - say: "remove fillers"
  - say: "remove silences"
  - say: "make coffee please"
  - say: "remove nonexistentphrase"
""".strip(),
            encoding="utf-8",
        )

        out_dir = tmp_path / "fillers_out"
        summary = run_session(session_yaml, out_dir, render_preset="preview")

        assert summary["status"] == "success"
        assert summary["steps_count"] == 4

        results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
        # Step 1: Remove fillers -> applied
        assert results[0]["status"] == "applied"
        assert results[0]["operations"][0]["op_type"] == "remove_fillers"

        # Step 2: Remove silences -> applied
        assert results[1]["status"] == "applied"
        assert results[1]["operations"][0]["op_type"] == "remove_silences"

        # Step 3: make coffee -> rejected
        assert results[2]["status"] == "rejected"
        assert "unrecognized" in results[2]["summary"].lower()

        # Step 4: nonexistentphrase -> rejected
        assert results[3]["status"] == "rejected"
        assert "not found" in results[3]["summary"].lower()
