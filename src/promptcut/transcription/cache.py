"""
Transcript Disk Cache.

Caches transcripts on disk keyed by (source_file_sha256 + model_name).
Prevents redundant transcription calls on identical media assets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from promptcut.media.intake import compute_file_sha256
from promptcut.models.transcript import Transcript

DEFAULT_CACHE_DIR = Path(".cache/transcripts")


def get_cache_dir() -> Path:
    """Return the transcript cache directory (honoring TRANSCRIPT_CACHE_DIR env)."""
    env_dir = os.environ.get("TRANSCRIPT_CACHE_DIR")
    cache_path = Path(env_dir) if env_dir else DEFAULT_CACHE_DIR
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def get_cache_key(source_hash: str, model_name: str) -> str:
    """Canonical filename key for a media file + model."""
    safe_model = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in model_name)
    return f"{source_hash}_{safe_model}.json"


def load_cached_transcript(
    media_path: str | Path,
    model_name: str,
    cache_dir: Path | None = None,
    source_hash: str | None = None,
) -> Transcript | None:
    """
    Load a cached Transcript from disk if present.

    Returns None on cache miss.
    """
    s_hash = source_hash or compute_file_sha256(media_path)
    c_dir = cache_dir or get_cache_dir()
    cache_file = c_dir / get_cache_key(s_hash, model_name)

    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return Transcript.model_validate(data)
        except Exception:
            return None
    return None


def save_cached_transcript(
    media_path: str | Path,
    model_name: str,
    transcript: Transcript,
    cache_dir: Path | None = None,
    source_hash: str | None = None,
) -> Path:
    """Save a Transcript to disk cache."""
    s_hash = source_hash or compute_file_sha256(media_path)
    c_dir = cache_dir or get_cache_dir()
    cache_file = c_dir / get_cache_key(s_hash, model_name)
    c_dir.mkdir(parents=True, exist_ok=True)

    cache_file.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    return cache_file

