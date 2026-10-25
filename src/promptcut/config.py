"""
Global configuration constants for the promptcut engine.

WHY a single module for constants:
- TIME_POLICY is referenced in every response; making it a constant means a
  one-line change switches the entire engine's interpretation of user times.
- FLOAT_PRECISION guards the timeline_hash against floating-point drift across
  platforms; all floats are rounded to this many decimal places before hashing.
- CAPTION_MIN_DISPLAY_SECS is stated in the spec as a hard rule; keeping it
  here makes it easy to find and change during a live review.
"""

# ---------------------------------------------------------------------------
# Time interpretation policy
# ---------------------------------------------------------------------------
# "current"  – user-stated times ("at 1:20") refer to the CURRENT edited
#              timeline after all active operations have been applied.
# "original" – times refer to the original unedited source video.
#
# Every engine response includes this constant in its payload so the caller
# always knows which coordinate system was used.
TIME_POLICY: str = "current"

# ---------------------------------------------------------------------------
# Caption display floor
# ---------------------------------------------------------------------------
# A caption whose surviving words would produce < this many seconds of screen
# time is extended to this duration.  Matches the spec requirement of 0.7 s.
CAPTION_MIN_DISPLAY_SECS: float = 0.7

# ---------------------------------------------------------------------------
# Hash precision
# ---------------------------------------------------------------------------
# All float values are rounded to FLOAT_PRECISION decimal places before they
# enter the canonical JSON that feeds timeline_hash.  6 d.p. ≈ 1 µs of
# precision, more than enough for video editing.
FLOAT_PRECISION: int = 6

# ---------------------------------------------------------------------------
# Intent resolution tolerance & models
# ---------------------------------------------------------------------------
import os
from pathlib import Path


def load_env_file(env_path: Path | None = None) -> None:
    """
    Pure-Python .env loader that populates os.environ without requiring python-dotenv.
    Does not overwrite variables already set in the host environment.
    """
    if env_path is None:
        candidates = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ]
        for cand in candidates:
            if cand.is_file():
                env_path = cand
                break
    if not env_path or not env_path.is_file():
        return

    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


# Automatically load .env on config module import
load_env_file()

# Maximum distance (in seconds) between user's time_hint and a candidate word
# occurrence to be considered a match.
TIME_HINT_TOLERANCE_SECS: float = 2.0

# Silence boundary snapping tolerance in seconds (default 150ms)
SILENCE_SNAP_TOLERANCE_S: float = 0.15

# Default model used for Groq interpreter when GROQ_MODEL is not set in env
DEFAULT_GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Interpreter mode: "auto" (default), "llm_only", or "rule_only"
INTERPRETER: str = os.getenv("INTERPRETER", "auto").strip().lower()

# Transcription mode: "auto" (default), "groq", "whisper", "vosk", or "mock"
TRANSCRIPTION_ADAPTER: str = os.getenv("TRANSCRIPTION_ADAPTER", "auto").strip().lower()


def get_interpreter_mode() -> str:
    """Return the active interpreter mode ('auto', 'llm_only', or 'rule_only')."""
    return os.getenv("INTERPRETER", "auto").strip().lower()


def get_transcription_adapter_mode() -> str:
    """Return active transcription adapter mode ('auto', 'groq', 'whisper', 'vosk', or 'mock')."""
    return os.getenv("TRANSCRIPTION_ADAPTER", "auto").strip().lower()


# Session storage directory (configurable via SESSIONS_DIR environment variable)
SESSIONS_DIR: Path = Path(os.getenv("SESSIONS_DIR", ".promptcut_sessions"))

