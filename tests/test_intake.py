"""
tests/test_intake.py

Unit tests for media intake and immutability checks using ffprobe.
"""

from pathlib import Path
import subprocess
import pytest

from promptcut.media.intake import (
    check_original_unchanged,
    compute_file_sha256,
    find_executable,
    inspect_media,
)


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a 2-second 320x240 test video with audio using ffmpeg lavfi."""
    tmp_dir = tmp_path_factory.mktemp("media")
    video_path = tmp_dir / "test_clip.mp4"

    ffmpeg_bin = find_executable("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", "2.0",
        str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return video_path


@pytest.fixture(scope="module")
def video_no_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a 1.5-second test video without audio."""
    tmp_dir = tmp_path_factory.mktemp("media_no_audio")
    video_path = tmp_dir / "no_audio.mp4"

    ffmpeg_bin = find_executable("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1.5:size=160x120:rate=24",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-an",
        "-t", "1.5",
        str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return video_path


def test_inspect_media_with_audio(synthetic_video: Path):
    meta = inspect_media(synthetic_video)
    assert meta.file_path == synthetic_video.resolve()
    assert pytest.approx(meta.duration, abs=0.2) == 2.0
    assert meta.width == 320
    assert meta.height == 240
    assert meta.fps == 25.0
    assert meta.has_audio is True
    assert len(meta.source_hash) == 64
    assert meta.file_size_bytes > 0


def test_inspect_media_no_audio(video_no_audio: Path):
    meta = inspect_media(video_no_audio)
    assert meta.width == 160
    assert meta.height == 120
    assert meta.has_audio is False
    assert meta.fps == 24.0


def test_check_original_unchanged(synthetic_video: Path, tmp_path: Path):
    # Copy file to a temp location so we can modify it
    temp_video = tmp_path / "copy_clip.mp4"
    temp_video.write_bytes(synthetic_video.read_bytes())

    original_hash = compute_file_sha256(temp_video)
    assert check_original_unchanged(original_hash, temp_video) is True

    # Mutate the copy
    with open(temp_video, "ab") as f:
        f.write(b"corruption_test")

    with pytest.raises(ValueError, match="corrupted or modified"):
        check_original_unchanged(original_hash, temp_video)


def test_inspect_media_file_not_found():
    with pytest.raises(FileNotFoundError):
        inspect_media("non_existent_file.mp4")
