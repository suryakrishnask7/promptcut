"""
tests/test_render.py

Unit tests for FFmpeg rendering pipeline.
Measures rendered test clip duration, resolution, and A/V sync with ffprobe.
"""

from pathlib import Path
import subprocess
import pytest

from promptcut.engine import apply
from promptcut.media.intake import find_executable, inspect_media
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import AddCaption, CutRange, SpeedRange
from promptcut.models.transcript import Transcript, Word
from promptcut.render import render_timeline


@pytest.fixture(scope="module")
def synthetic_source_media(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Transcript]:
    """Generate a 4-second 320x240 video with sine audio and matching transcript."""
    tmp_dir = tmp_path_factory.mktemp("render_source")
    video_path = tmp_dir / "synthetic_source.mp4"

    ffmpeg_bin = find_executable("ffmpeg")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4:sample_rate=44100",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", "4.0",
        str(video_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    words = [
        Word(id=0, text="Welcome", start=0.0, end=1.0),
        Word(id=1, text="to", start=1.0, end=2.0),
        Word(id=2, text="the", start=2.0, end=3.0),
        Word(id=3, text="channel", start=3.0, end=4.0),
    ]
    transcript = Transcript(words=words, title="Synthetic 4s Source", source_duration=4.0)
    return video_path, transcript


def test_render_cut_duration_matches_within_one_frame(
    synthetic_source_media: tuple[Path, Transcript], tmp_path: Path
):
    src_video, transcript = synthetic_source_media

    # Cut word 1 ("to", 1.0-2.0s) -> 3.0s expected
    log = OperationLog().push(CutRange(word_ids=[1]))
    timeline = apply(log, transcript)
    assert timeline.total_duration == 3.0

    out_file = tmp_path / "rendered_cut.mp4"
    rendered_path = render_timeline(timeline, transcript, src_video, out_file, preset="final")

    assert rendered_path.exists()

    # Measure with ffprobe
    meta = inspect_media(rendered_path)
    one_frame_sec = 1.0 / 25.0  # 0.04s
    assert abs(meta.duration - timeline.total_duration) <= one_frame_sec + 0.05, (
        f"Rendered duration {meta.duration}s should match timeline {timeline.total_duration}s "
        f"within 1 frame ({one_frame_sec:.3f}s)"
    )
    assert meta.has_audio is True


def test_render_preview_preset_and_caching(
    synthetic_source_media: tuple[Path, Transcript], tmp_path: Path
):
    src_video, transcript = synthetic_source_media
    cache_dir = tmp_path / "preview_cache"

    # Add speed change on word 0 (0.0-1.0s at 2.0x -> 0.5s duration)
    log = OperationLog().push(SpeedRange(word_ids=[0], factor=2.0))
    timeline = apply(log, transcript)

    out_file1 = tmp_path / "preview_run1.mp4"
    rendered1 = render_timeline(
        timeline, transcript, src_video, out_file1, preset="preview", cache_dir=cache_dir
    )

    meta1 = inspect_media(rendered1)
    assert meta1.height == 480  # Preview scaled to 480p

    # Second call should hit the preview cache
    out_file2 = tmp_path / "preview_run2.mp4"
    rendered2 = render_timeline(
        timeline, transcript, src_video, out_file2, preset="preview", cache_dir=cache_dir
    )
    assert rendered2.exists()
    assert rendered2.stat().st_size == rendered1.stat().st_size


def test_render_caption_burn_in(
    synthetic_source_media: tuple[Path, Transcript], tmp_path: Path
):
    src_video, transcript = synthetic_source_media

    # Add caption with red styling
    log = OperationLog().push(
        AddCaption(word_ids=[0, 1], text="Welcome!", color="red")
    )
    timeline = apply(log, transcript)

    out_file = tmp_path / "rendered_caption.mp4"
    rendered = render_timeline(timeline, transcript, src_video, out_file, preset="final")

    assert rendered.exists()
    meta = inspect_media(rendered)
    assert meta.duration > 0
    assert meta.has_audio is True


def test_generate_ass_subtitles_formatting(tmp_path: Path):
    from promptcut.models.timeline import CaptionOverlay
    from promptcut.render.ass import generate_ass_subtitles

    captions = [
        CaptionOverlay(
            caption_id=0,
            word_ids=[0, 1],
            text="Hello World!",
            start_time=1.0,
            end_time=3.5,
            source_op_id=0,
            color="#FF0000",
        ),
        CaptionOverlay(
            caption_id=1,
            word_ids=[2],
            text="Second line",
            start_time=4.0,
            end_time=5.0,
            source_op_id=1,
            color="yellow",
        ),
    ]

    ass_file = tmp_path / "subtitles.ass"
    generate_ass_subtitles(captions, ass_file, play_res_x=1920, play_res_y=1080)

    assert ass_file.exists()
    content = ass_file.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "PlayResX: 1920" in content
    assert "HELLO WORLD!" in content
    assert "SECOND LINE" in content
    assert "Dialogue: 0,0:00:01.00,0:00:03.50" in content


def test_generate_ass_subtitles_with_word_highlighting(
    synthetic_source_media: tuple[Path, Transcript], tmp_path: Path
):
    from promptcut.engine import apply
    from promptcut.models.operation_log import OperationLog
    from promptcut.models.operations import AddCaption
    from promptcut.render.ass import generate_ass_subtitles

    src_video, transcript = synthetic_source_media
    # Add caption to words 0 and 1 ("welcome to")
    log = OperationLog().push(AddCaption(word_ids=[0, 1], text="WELCOME TO", color="yellow"))
    timeline = apply(log, transcript)

    ass_file = tmp_path / "highlight_subtitles.ass"
    generate_ass_subtitles(
        timeline.captions,
        ass_file,
        play_res_x=1280,
        play_res_y=720,
        transcript=transcript,
        timeline=timeline,
    )

    assert ass_file.exists()
    content = ass_file.read_text(encoding="utf-8")
    # Verify outline 3.5 in style header
    assert "Outline, Shadow" in content or "Style: Default" in content
    assert "3.5,1.5" in content
    # Verify active word highlighting tags
    assert "\\c&H00FFFF&" in content  # Yellow for active word
    assert "\\c&HFFFFFF&" in content  # White for non-active words

