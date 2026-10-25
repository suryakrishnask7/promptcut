"""
faster-whisper Transcription Adapter.

Wraps faster-whisper (SYSTRAN) to extract word-level timestamps and confidences.
Model configuration is driven by environment variables:
- WHISPER_MODEL (default: "base")
- WHISPER_DEVICE (default: "cpu")
- WHISPER_COMPUTE_TYPE (default: "int8")

Licensing:
- faster-whisper library: MIT License
- CTranslate2 engine: MIT License
- OpenAI Whisper model weights: MIT License
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from promptcut.models.transcript import Transcript, Word
from promptcut.transcription.base import TranscriptionAdapter
from promptcut.transcription.cache import load_cached_transcript, save_cached_transcript


class FasterWhisperAdapter(TranscriptionAdapter):
    """
    Transcription provider using faster-whisper for accurate word-level timing.
    """

    def __init__(
        self,
        model_size_or_path: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ):
        self._model_name = (
            model_size_or_path
            or os.environ.get("WHISPER_MODEL")
            or "base"
        )
        self._device = (
            device
            or os.environ.get("WHISPER_DEVICE")
            or "cpu"
        )
        self._compute_type = (
            compute_type
            or os.environ.get("WHISPER_COMPUTE_TYPE")
            or ("int8" if self._device == "cpu" else "float16")
        )
        self._use_cache = use_cache
        self._cache_dir = cache_dir
        self._model: Any = None

    @property
    def model_name(self) -> str:
        return f"faster-whisper-{self._model_name}"

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError as e:
                raise ImportError(
                    "faster-whisper is not installed. Install it with: "
                    "`pip install faster-whisper`"
                ) from e
            self._model = WhisperModel(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
            )
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

        model = self._get_model()
        segments, info = model.transcribe(
            str(path),
            language=language,
            word_timestamps=True,
        )

        words: list[Word] = []
        wid = 0
        for seg in segments:
            if not getattr(seg, "words", None):
                continue
            seg_words = [w for w in seg.words if w.word.strip()]
            seg_text = getattr(seg, "text", "") or ""
            seg_has_terminal = bool(seg_text.strip() and seg_text.strip()[-1] in ".!?")

            for i, w in enumerate(seg_words):
                text = w.word.strip()
                conf = getattr(w, "probability", 0.95)
                is_last_in_seg = (i == len(seg_words) - 1)
                ends_with_terminal = bool(text and text[-1] in ".!?")
                is_sentence_end = ends_with_terminal or (is_last_in_seg and seg_has_terminal)

                words.append(
                    Word(
                        id=wid,
                        text=text,
                        start=round(float(w.start), 3),
                        end=round(float(w.end), 3),
                        speaker="S1",
                        confidence=round(float(conf), 3),
                        sentence_end=is_sentence_end,
                    )
                )
                wid += 1

        transcript = Transcript(
            words=words,
            title=path.stem,
            source_duration=round(float(info.duration), 3) if info else 0.0,
        )

        if self._use_cache:
            save_cached_transcript(
                path, self.model_name, transcript, self._cache_dir, source_hash=source_hash
            )

        return transcript

