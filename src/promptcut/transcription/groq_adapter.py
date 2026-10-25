"""
Groq Whisper Cloud Transcription Adapter.

Wraps Groq's ultra-fast Whisper API (whisper-large-v3-turbo or whisper-large-v3)
to extract accurate word-level timestamps and confidences from audio/video streams.
Supports long videos (1 hour, 2 hours, 3+ hours) via smart 32kbps mono compression
and automatic 20-minute chunking with seamless timestamp stitching.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from promptcut.media.intake import find_executable, inspect_media
from promptcut.models.transcript import Transcript, Word
from promptcut.transcription.base import TranscriptionAdapter
from promptcut.transcription.cache import load_cached_transcript, save_cached_transcript

logger = logging.getLogger(__name__)

# 20 minutes per chunk (well within Groq's 25 MB payload limit at 32kbps mono)
DEFAULT_CHUNK_SECONDS = 1200.0
MAX_SINGLE_FILE_SECONDS = 2700.0  # 45 minutes
MAX_FILE_BYTES = 22 * 1024 * 1024  # 22 MB safety limit for single request


class GroqWhisperAdapter(TranscriptionAdapter):
    """
    Cloud transcription provider using Groq's high-speed Whisper endpoints.
    Handles long recordings seamlessly without hitting payload or timeout limits.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ):
        self._api_key = api_key or os.environ.get("GROQ_API_KEY")
        self._model_name = (
            model_name
            or os.environ.get("GROQ_WHISPER_MODEL")
            or "whisper-large-v3-turbo"
        )
        self._use_cache = use_cache
        self._cache_dir = cache_dir

    @property
    def model_name(self) -> str:
        return f"groq-{self._model_name}"

    def transcribe(
        self,
        media_path: str | Path,
        language: str = "en",
        source_hash: str | None = None,
    ) -> Transcript:
        path = Path(media_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source media not found: {path}")

        # Check disk cache first
        if self._use_cache:
            if cached := load_cached_transcript(
                path, self.model_name, self._cache_dir, source_hash=source_hash
            ):
                # Auto-upgrade cached transcripts that only have default/unassigned or generic speakers
                unique_speakers = {w.speaker for w in cached.words if w.speaker}
                if unique_speakers <= {"S1", "Speaker 1", "Speaker 2"} or not unique_speakers:
                    from promptcut.transcription.diarization import diarize_transcript_speakers

                    cached = diarize_transcript_speakers(cached, media_path=path, api_key=self._api_key)
                    save_cached_transcript(
                        path, self.model_name, cached, self._cache_dir, source_hash=source_hash
                    )
                return cached

        if not self._api_key:
            raise ValueError("GROQ_API_KEY must be provided to use GroqWhisperAdapter.")

        try:
            from groq import Groq
        except ImportError as e:
            raise ImportError(
                "groq is not installed. Install it with: `pip install groq`"
            ) from e

        # Configure Groq client with a generous 180s timeout for large payload transfers
        client = Groq(api_key=self._api_key, timeout=180.0)
        ffmpeg_bin = find_executable("ffmpeg")

        # Probe duration of media
        total_duration = 0.0
        try:
            meta = inspect_media(path)
            total_duration = float(meta.duration)
        except Exception as e:
            logger.warning(f"Failed to inspect media duration via ffprobe: {e}")

        with tempfile.TemporaryDirectory() as td:
            temp_dir = Path(td)

            # Strategy 1: If file is short enough (<= 45 min), try single-file extraction
            if 0 < total_duration <= MAX_SINGLE_FILE_SECONDS:
                audio_path = temp_dir / "audio.m4a"
                self._extract_audio(ffmpeg_bin, path, audio_path)

                if audio_path.stat().st_size <= MAX_FILE_BYTES:
                    raw_res = self._call_groq_whisper(client, audio_path, language)
                    words = self._parse_words(raw_res, time_offset=0.0)
                    duration = getattr(raw_res, "duration", None) or total_duration or (words[-1].end if words else 0.0)
                    transcript = Transcript(
                        words=words,
                        title=path.stem,
                        source_duration=round(float(duration), 3),
                    )
                    from promptcut.transcription.diarization import diarize_transcript_speakers
                    transcript = diarize_transcript_speakers(transcript, media_path=path, api_key=self._api_key)

                    if self._use_cache:
                        save_cached_transcript(
                            path, self.model_name, transcript, self._cache_dir, source_hash=source_hash
                        )
                    return transcript

            # Strategy 2: Long file (> 45 min or > 22 MB) -> Chunked processing
            if total_duration <= 0:
                # If duration wasn't known from inspect_media, extract whole audio once to probe
                audio_full = temp_dir / "full_audio.m4a"
                self._extract_audio(ffmpeg_bin, path, audio_full)
                if audio_full.stat().st_size <= MAX_FILE_BYTES:
                    raw_res = self._call_groq_whisper(client, audio_full, language)
                    words = self._parse_words(raw_res, time_offset=0.0)
                    duration = getattr(raw_res, "duration", None) or (words[-1].end if words else 0.0)
                    transcript = Transcript(
                        words=words,
                        title=path.stem,
                        source_duration=round(float(duration), 3),
                    )
                    from promptcut.transcription.diarization import diarize_transcript_speakers
                    transcript = diarize_transcript_speakers(transcript, media_path=path, api_key=self._api_key)

                    if self._use_cache:
                        save_cached_transcript(
                            path, self.model_name, transcript, self._cache_dir, source_hash=source_hash
                        )
                    return transcript
                # Still large: probe extracted audio duration
                try:
                    meta_a = inspect_media(audio_full)
                    total_duration = float(meta_a.duration)
                except Exception:
                    total_duration = 3600.0  # fallback assumption

            chunk_len = DEFAULT_CHUNK_SECONDS
            num_chunks = max(1, math.ceil(total_duration / chunk_len))
            logger.info(
                f"Transcribing {total_duration:.1f}s video ({total_duration/60:.1f} min) "
                f"in {num_chunks} chunks of {chunk_len/60:.0f} min each..."
            )

            all_words: list[Word] = []

            for k in range(num_chunks):
                offset = k * chunk_len
                cur_len = min(chunk_len, total_duration - offset)
                if cur_len <= 0.1:
                    break

                chunk_file = temp_dir / f"chunk_{k}.m4a"
                self._extract_audio(
                    ffmpeg_bin,
                    path,
                    chunk_file,
                    start_time=offset,
                    duration=cur_len,
                )

                logger.info(f"Sending chunk {k+1}/{num_chunks} (offset {offset:.1f}s, length {cur_len:.1f}s) to Groq...")
                raw_res = self._call_groq_whisper(client, chunk_file, language)
                chunk_words = self._parse_words(raw_res, time_offset=offset, start_id=len(all_words))
                all_words.extend(chunk_words)

            transcript = Transcript(
                words=all_words,
                title=path.stem,
                source_duration=round(total_duration, 3) if total_duration > 0 else (all_words[-1].end if all_words else 0.0),
            )

            from promptcut.transcription.diarization import diarize_transcript_speakers
            transcript = diarize_transcript_speakers(transcript, media_path=path, api_key=self._api_key)

            if self._use_cache:
                save_cached_transcript(
                    path, self.model_name, transcript, self._cache_dir, source_hash=source_hash
                )

            return transcript

    def _extract_audio(
        self,
        ffmpeg_bin: str,
        input_path: Path,
        output_path: Path,
        start_time: float | None = None,
        duration: float | None = None,
    ) -> None:
        """Extract lightweight 16kHz mono 32kbps AAC audio from source."""
        cmd = [ffmpeg_bin, "-y"]
        if start_time is not None:
            cmd.extend(["-ss", str(round(start_time, 3))])
        if duration is not None:
            cmd.extend(["-t", str(round(duration, 3))])

        cmd.extend([
            "-i", str(input_path),
            "-vn",
            "-c:a", "aac",
            "-ar", "16000",
            "-ac", "1",
            "-b:a", "32k",
            str(output_path),
        ])

        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _call_groq_whisper(self, client: Any, audio_path: Path, language: str) -> Any:
        """Call Groq Whisper API endpoint with word-level timestamps."""
        with open(audio_path, "rb") as f:
            return client.audio.transcriptions.create(
                file=("audio.m4a", f.read()),
                model=self._model_name,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language=language,
            )

    def _parse_words(self, res: Any, time_offset: float = 0.0, start_id: int = 0) -> list[Word]:
        """Parse raw Groq verbose_json output into strongly-typed Word models."""
        words: list[Word] = []
        raw_words = getattr(res, "words", None) or []

        if not raw_words and getattr(res, "segments", None):
            for seg in res.segments:
                seg_w = getattr(seg, "words", None) or []
                raw_words.extend(seg_w)

        for w in raw_words:
            if isinstance(w, dict):
                text = str(w.get("word", "")).strip()
                start = float(w.get("start", 0.0))
                end = float(w.get("end", 0.0))
                conf = float(w.get("confidence", 0.98))
            else:
                text = str(getattr(w, "word", "")).strip()
                start = float(getattr(w, "start", 0.0))
                end = float(getattr(w, "end", 0.0))
                conf = float(getattr(w, "confidence", 0.98))

            if not text:
                continue

            ends_with_terminal = bool(text[-1] in ".!?")
            words.append(
                Word(
                    id=start_id + len(words),
                    text=text,
                    start=round(time_offset + start, 3),
                    end=round(time_offset + end, 3),
                    speaker="S1",
                    confidence=round(conf, 3),
                    sentence_end=ends_with_terminal,
                )
            )

        return words
