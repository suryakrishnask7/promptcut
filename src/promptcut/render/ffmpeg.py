"""
FFmpeg Timeline Renderer.

Compiles a promptcut Timeline into an accurate FFmpeg filtergraph:
- Segment cutting and time scaling (setpts + chained atempo)
- Audio muting (volume=0) and bleeping (sine tone synthesis)
- Seamless multi-segment concatenation (concat filter)
- Subtitle burn-in via ASS filter (supporting custom colors and font styles)
- Preview preset (480p ultrafast, cached by timeline_hash) and Final preset (high quality)
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Callable, Literal
import uuid

from promptcut.media.intake import find_executable, inspect_media
from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript
from promptcut.render.ass import generate_ass_subtitles


def _build_atempo_filter(factor: float) -> str:
    """
    Generate chained atempo audio filters for arbitrary speed factors.

    FFmpeg's atempo filter only supports 0.5 to 2.0 per instance.
    Speeds outside that range (e.g. 4.0x or 0.25x) are factored iteratively.
    """
    if abs(factor - 1.0) < 0.001:
        return ""

    filters: list[str] = []
    rem = factor
    while rem > 2.0:
        filters.append("atempo=2.0")
        rem /= 2.0
    while rem < 0.5:
        filters.append("atempo=0.5")
        rem /= 0.5

    if abs(rem - 1.0) >= 0.001:
        filters.append(f"atempo={rem:.4f}")

    return ",".join(filters)


def _escape_filter_path(p: Path) -> str:
    """Escape file path for FFmpeg filter arguments on Windows and POSIX."""
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def render_timeline(
    timeline: Timeline,
    transcript: Transcript,
    source_media_path: str | Path,
    out_path: str | Path,
    preset: Literal["preview", "final"] = "final",
    cache_dir: Path | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """
    Render an edited video from Timeline using FFmpeg.

    Parameters
    ----------
    timeline : Timeline
        Compiled output timeline from engine.apply().
    transcript : Transcript
        Source transcript providing word timing anchors.
    source_media_path : str | Path
        Path to untouched original video file.
    out_path : str | Path
        Destination path for rendered video.
    preset : 'preview' | 'final'
        'preview' (480p, ultrafast, cached by timeline_hash) or 'final' (full quality).
    cache_dir : Path | None
        Directory to store preview cache.

    Returns
    -------
    Path
        Path to rendered output video file.
    """
    src_path = Path(source_media_path).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source media not found: {src_path}")

    destination = Path(out_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    # 1. Check preview cache by timeline_hash
    if preset == "preview":
        c_dir = cache_dir or Path(".cache/renders")
        c_dir.mkdir(parents=True, exist_ok=True)
        cached_preview = c_dir / f"{timeline.timeline_hash}_preview.mp4"
        if cached_preview.exists():
            shutil.copyfile(cached_preview, destination)
            return destination

    if not timeline.segments:
        raise ValueError("Cannot render an empty timeline (no segments).")

    ffmpeg_bin = find_executable("ffmpeg")

    # 2. Generate ASS subtitles for burn-in if captions exist
    ass_path: Path | None = None
    if timeline.captions:
        ass_path = destination.parent / f"{destination.stem}_captions.ass"
        meta = inspect_media(src_path)
        w = meta.width if meta.width > 0 else 1280
        h = meta.height if meta.height > 0 else 720
        generate_ass_subtitles(
            timeline.captions,
            ass_path,
            play_res_x=w,
            play_res_y=h,
            transcript=transcript,
            timeline=timeline,
        )

    # 3. Build filtergraph segments
    filter_chains: list[str] = []
    v_segment_tags: list[str] = []
    a_segment_tags: list[str] = []

    for i, seg in enumerate(timeline.segments):
        words = [w for wid in seg.source_word_ids if (w := transcript.word_by_id(wid)) is not None]
        src_start = words[0].start if words else 0.0
        src_end = words[-1].end if words else seg.end_time

        # Video filter for segment
        v_tag = f"[v{i}]"
        v_filters: list[str] = [f"trim=start={src_start:.3f}:end={src_end:.3f}"]
        if abs(seg.zoom_factor - 1.0) > 0.001:
            # Punch-in / speaker zoom: crop center region and scale back to preserve dimensions
            v_filters.append(f"crop=iw/{seg.zoom_factor:.4f}:ih/{seg.zoom_factor:.4f},scale=iw:ih")
        if abs(seg.speed_factor - 1.0) > 0.001:
            v_filters.append(f"setpts=(PTS-STARTPTS)/{seg.speed_factor:.4f}")
        else:
            v_filters.append("setpts=PTS-STARTPTS")

        filter_chains.append(f"[0:v]{','.join(v_filters)}{v_tag}")
        v_segment_tags.append(v_tag)

        # Audio filter for segment
        a_tag = f"[a{i}]"
        a_filters = [f"atrim=start={src_start:.3f}:end={src_end:.3f}", "asetpts=PTS-STARTPTS"]
        if abs(seg.speed_factor - 1.0) > 0.001:
            atempo = _build_atempo_filter(seg.speed_factor)
            if atempo:
                a_filters.append(atempo)

        if seg.is_muted:
            a_filters.append("volume=0")
        elif seg.is_bleeped:
            # Volume down original audio and synthesize 1000Hz bleep tone
            a_filters.append("volume=0,aeval=val(0)+0.3*sin(2*PI*1000*t)")

        filter_chains.append(f"[0:a]{','.join(a_filters)}{a_tag}")
        a_segment_tags.append(a_tag)

    n_segs = len(timeline.segments)
    concat_inputs = "".join(f"{v_segment_tags[i]}{a_segment_tags[i]}" for i in range(n_segs))
    filter_chains.append(f"{concat_inputs}concat=n={n_segs}:v=1:a=1[v_concat][a_out]")

    # 4. Burn-in subtitles and scale if preview
    v_final = "[v_concat]"
    if ass_path and ass_path.exists():
        escaped_ass = _escape_filter_path(ass_path)
        filter_chains.append(f"{v_final}subtitles='{escaped_ass}'[v_sub]")
        v_final = "[v_sub]"

    if preset == "preview":
        filter_chains.append(f"{v_final}scale=-2:480[v_out]")
    else:
        filter_chains.append(f"{v_final}null[v_out]")

    filtergraph_str = ";\n".join(filter_chains)

    # 5. Execute FFmpeg
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(src_path),
        "-filter_complex", filtergraph_str,
        "-map", "[v_out]",
        "-map", "[a_out]",
    ]

    prog_file: Path | None = None
    if on_progress is not None:
        prog_file = destination.parent / f"{destination.stem}_{uuid.uuid4().hex[:6]}_prog.txt"
        cmd.extend(["-progress", str(prog_file), "-nostats", "-loglevel", "error"])
    else:
        cmd.extend(["-loglevel", "error"])

    if preset == "preview":
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "96k",
        ])
    else:
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
        ])

    cmd.append(str(destination))

    if on_progress is not None and prog_file is not None:
        err_file = destination.parent / f"{destination.stem}_{uuid.uuid4().hex[:6]}_err.log"
        with open(err_file, "w", encoding="utf-8", errors="ignore") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=err_f,
                text=True,
            )
            tot_dur = max(0.1, timeline.total_duration)
            while proc.poll() is None:
                time.sleep(0.3)
                if prog_file.is_file():
                    try:
                        content = prog_file.read_text(errors="ignore")
                        lines = content.strip().splitlines()
                        for l in reversed(lines[-8:]):
                            if l.startswith(("out_time_us=", "out_time_ms=")):
                                val = int(l.split("=")[1])
                                secs = val / 1_000_000.0
                                pct = min(98.0, max(5.0, (secs / tot_dur) * 100.0))
                                on_progress(pct)
                                break
                    except Exception:
                        pass

        if proc.returncode != 0:
            stderr_text = err_file.read_text(encoding="utf-8", errors="ignore") if err_file.is_file() else ""
            raise RuntimeError(
                f"FFmpeg render failed with exit code {proc.returncode}:\n{stderr_text}"
            )
        on_progress(100.0)
        try:
            if prog_file.exists():
                prog_file.unlink()
            if err_file.exists():
                err_file.unlink()
        except OSError:
            pass
    else:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"FFmpeg render failed with exit code {res.returncode}:\n{res.stderr}"
            )

    # Save to preview cache if preset is preview
    if preset == "preview" and destination.exists():
        c_dir = cache_dir or Path(".cache/renders")
        cached_preview = c_dir / f"{timeline.timeline_hash}_preview.mp4"
        shutil.copyfile(destination, cached_preview)

    # Clean up temp ASS subtitle file
    if ass_path and ass_path.exists():
        try:
            ass_path.unlink()
        except OSError:
            pass

    return destination
