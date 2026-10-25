"""
Media Intake & Source Immutability.

Wraps ffprobe to extract stream metadata (duration, fps, resolution,
audio presence, variable-frame-rate flag) and computes SHA-256 hashes
to enforce IncuBrix Must-Have 1: "Never damage the original".
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from pydantic import BaseModel, Field


def find_executable(name: str) -> str:
    """
    Locate an executable binary (ffmpeg or ffprobe).

    Checks:
    1. System PATH via shutil.which
    2. Environment variable (FFMPEG_PATH / FFPROBE_PATH)
    3. Windows WinGet package default install paths
    """
    env_var = f"{name.upper()}_PATH"
    if env_val := os.environ.get(env_var):
        if Path(env_val).exists():
            return env_val

    if path_bin := shutil.which(name):
        return path_bin

    # Search WinGet local package directory on Windows
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_pattern = os.path.join(
            local_app_data,
            "Microsoft",
            "WinGet",
            "Packages",
            "Gyan.FFmpeg*",
            "ffmpeg*",
            "bin",
            f"{name}.exe",
        )
        matches = glob.glob(winget_pattern)
        if matches and Path(matches[0]).exists():
            return matches[0]

    raise FileNotFoundError(
        f"Could not locate {name}. Please ensure {name} is installed and in PATH, "
        f"or set {env_var}."
    )


class MediaMetadata(BaseModel):
    """
    Extracted media properties from ffprobe inspection.
    """

    file_path: Path
    duration: float = Field(ge=0.0)
    fps: float = Field(gt=0.0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    has_audio: bool
    is_vfr: bool = False
    source_hash: str
    file_size_bytes: int = Field(ge=0)


def compute_file_sha256(path: str | Path) -> str:
    """Compute the SHA-256 checksum of a file in streaming chunks."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Source file not found: {p}")

    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_original_unchanged(
    arg1: str | Path,
    arg2: str | Path,
) -> bool:
    """
    Verify that the source media file on disk has not been altered or corrupted.

    Enforces IncuBrix Must-Have 1: "Never damage the original".
    Accepts (original_hash, file_path) or (file_path, original_hash).
    Returns True if hash matches; raises ValueError if modified.
    """
    # Detect which argument is the path and which is the sha256 hash
    if isinstance(arg1, Path) or (isinstance(arg1, str) and (Path(arg1).is_file() or "/" in arg1 or "\\" in arg1)):
        file_path, original_hash = Path(arg1), str(arg2)
    else:
        original_hash, file_path = str(arg1), Path(arg2)

    current_hash = compute_file_sha256(file_path)
    if current_hash != original_hash:
        raise ValueError(
            f"Original media corrupted or modified! Original hash: {original_hash}, "
            f"Current hash: {current_hash} for file {file_path}"
        )
    return True


def _parse_fps(rate_str: str) -> float:
    """Convert fraction string like '24/1' or '30000/1001' to float."""
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/", 1)
            num_f, den_f = float(num), float(den)
            return round(num_f / den_f, 3) if den_f != 0 else 0.0
        return round(float(rate_str), 3)
    except Exception:
        return 0.0


def inspect_media(media_path: str | Path) -> MediaMetadata:
    """
    Inspect a media file using ffprobe and calculate its SHA-256 hash.

    Parameters
    ----------
    media_path : str | Path
        Path to video or audio file.

    Returns
    -------
    MediaMetadata
        Validated metadata containing duration, fps, resolution, audio presence,
        VFR flag, and raw file hash.
    """
    path = Path(media_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Media file not found: {path}")

    ffprobe_bin = find_executable("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    probe_data = json.loads(result.stdout)

    streams = probe_data.get("streams", [])
    format_info = probe_data.get("format", {})

    video_stream: dict[str, Any] | None = None
    has_audio = False

    for st in streams:
        codec_type = st.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = st
        elif codec_type == "audio":
            has_audio = True

    duration = 0.0
    if "duration" in format_info:
        try:
            duration = float(format_info["duration"])
        except ValueError:
            pass

    width = 0
    height = 0
    fps = 24.0
    is_vfr = False

    if video_stream:
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))

        r_fps = _parse_fps(video_stream.get("r_frame_rate", "24/1"))
        avg_fps = _parse_fps(video_stream.get("avg_frame_rate", "24/1"))

        fps = avg_fps if avg_fps > 0 else (r_fps if r_fps > 0 else 24.0)

        # Detect Variable Frame Rate (VFR): discrepancy between average and nominal rate
        if r_fps > 0 and avg_fps > 0 and abs(r_fps - avg_fps) > 0.05:
            is_vfr = True

        if duration == 0.0 and "duration" in video_stream:
            try:
                duration = float(video_stream["duration"])
            except ValueError:
                pass

    source_hash = compute_file_sha256(path)
    file_size = int(format_info.get("size", path.stat().st_size))

    return MediaMetadata(
        file_path=path,
        duration=round(duration, 3),
        fps=fps,
        width=width,
        height=height,
        has_audio=has_audio,
        is_vfr=is_vfr,
        source_hash=source_hash,
        file_size_bytes=file_size,
    )
