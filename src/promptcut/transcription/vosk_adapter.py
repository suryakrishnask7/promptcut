"""
Vosk Offline Transcription Adapter.

Wraps Vosk (Apache 2.0) speech recognition engine to produce frame-accurate
word-level timestamps and confidences on Windows/macOS/Linux without external
cloud APIs or PyTorch/CUDA dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from promptcut.media.intake import find_executable, inspect_media
from promptcut.models.transcript import Transcript, Word
from promptcut.transcription.base import TranscriptionAdapter
from promptcut.transcription.cache import load_cached_transcript, save_cached_transcript


class VoskTranscriptionAdapter(TranscriptionAdapter):
    """
    Offline speech recognition provider using Vosk with Kaldi word alignment.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        lang: str = "en-us",
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ):
        self._model_path = model_path
        self._lang = lang
        self._use_cache = use_cache
        self._cache_dir = cache_dir
        self._model: Any = None

    @property
    def model_name(self) -> str:
        return f"vosk-{self._lang}"

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                import vosk  # type: ignore
            except ImportError as e:
                raise ImportError(
                    "vosk is not installed. Install it with: `pip install vosk`"
                ) from e

            # Suppress noisy Kaldi C++ engine logs
            try:
                vosk.SetLogLevel(-1)
            except Exception:
                pass

            if self._model_path:
                self._model = vosk.Model(str(self._model_path))
            else:
                self._model = vosk.Model(lang=self._lang)
        return self._model

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
                return cached

        # Inspect duration via ffprobe
        duration = 0.0
        try:
            meta = inspect_media(path)
            duration = meta.duration
        except Exception:
            pass

        model = self._get_model()
        import vosk

        ffmpeg_bin = find_executable("ffmpeg")
        cmd = [
            ffmpeg_bin,
            "-loglevel", "quiet",
            "-i", str(path),
            "-ar", "16000",
            "-ac", "1",
            "-f", "s16le",
            "-",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        rec = vosk.KaldiRecognizer(model, 16000)
        rec.SetWords(True)

        raw_words: list[dict[str, Any]] = []
        try:
            while True:
                data = proc.stdout.read(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    part = json.loads(rec.Result())
                    if "result" in part:
                        raw_words.extend(part["result"])

            final_part = json.loads(rec.FinalResult())
            if "result" in final_part:
                raw_words.extend(final_part["result"])
        finally:
            proc.stdout.close()
            proc.wait()

        # Map to Promptcut Word objects with sequential IDs
        words: list[Word] = []
        current_speaker = "S1"
        last_end = 0.0

        for wid, rw in enumerate(raw_words):
            word_text = rw.get("word", "").strip()
            if not word_text:
                continue
            start_t = round(float(rw.get("start", 0.0)), 3)
            end_t = round(float(rw.get("end", start_t + 0.2)), 3)
            conf = round(float(rw.get("conf", 0.95)), 3)

            # Heuristic speaker turn switch on natural pause > 1.8s
            if start_t - last_end > 1.8 and wid > 0:
                current_speaker = "S2" if current_speaker == "S1" else "S1"

            words.append(
                Word(
                    id=len(words),
                    text=word_text,
                    start=start_t,
                    end=end_t,
                    speaker=current_speaker,
                    confidence=conf,
                )
            )
            last_end = end_t

        if not words and duration > 0:
            # If no speech was detected in the audio track (silent or music only)
            # generate fallback words for the duration
            pass

        final_dur = duration if duration > 0 else (words[-1].end + 0.5 if words else 1.0)
        transcript = Transcript(
            words=words,
            title=path.stem,
            source_duration=round(final_dur, 3),
        )

        if self._use_cache:
            save_cached_transcript(
                path, self.model_name, transcript, self._cache_dir, source_hash=source_hash
            )

        return transcript
