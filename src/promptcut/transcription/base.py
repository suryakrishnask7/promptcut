"""
Transcription Adapter Base Interface.

Defines the contract for speech-to-text providers (faster-whisper, mock).
Ensures output words are mapped into the deterministic promptcut Transcript model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from promptcut.models.transcript import Transcript


class TranscriptionAdapter(ABC):
    """Abstract base class for transcription providers."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name of the model or mock identifier for cache keying."""
        ...

    @abstractmethod
    def transcribe(
        self,
        media_path: str | Path,
        language: str = "en",
        source_hash: str | None = None,
    ) -> Transcript:
        """
        Transcribe an audio/video file into a promptcut Transcript.

        Parameters
        ----------
        media_path : str | Path
            Path to media file.
        language : str
            Language code (default: 'en').

        Returns
        -------
        Transcript
            Transcript with sequential word IDs (0, 1, 2, ...), timestamps,
            and confidence scores.
        """
        ...
