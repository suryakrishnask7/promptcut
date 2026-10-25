"""
Speaker Diarization Adapter & JSON Sidecar Reader.

Provides an adapter interface and sidecar file loader to annotate transcript
words with speaker labels (S1, S2, host, guest) prior to full diarization.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
from pathlib import Path
from typing import Any

from promptcut.models.transcript import Transcript, Word


class DiarizationAdapter(ABC):
    """Abstract interface for speaker diarization providers."""

    @abstractmethod
    def apply_speakers(
        self, transcript: Transcript, sidecar_path: str | Path | None = None
    ) -> Transcript:
        """
        Annotate transcript words with speaker IDs.

        Parameters
        ----------
        transcript : Transcript
            Input transcript.
        sidecar_path : str | Path | None
            Optional path to external diarization sidecar JSON.

        Returns
        -------
        Transcript
            New Transcript instance with updated speaker tags.
        """
        ...


class MockSidecarDiarizationAdapter(DiarizationAdapter):
    """
    Diarization provider reading speaker labels from a JSON sidecar file.

    Supported JSON sidecar formats:
    -------------------------------
    1. Intervals format:
       [
         {"start": 0.0, "end": 14.5, "speaker": "S1"},
         {"start": 14.5, "end": 30.0, "speaker": "S2"}
       ]
       or {"intervals": [...]}

    2. Direct word ID map:
       {"word_speakers": {"0": "S1", "1": "S2"}}
    """

    def apply_speakers(
        self, transcript: Transcript, sidecar_path: str | Path | None = None
    ) -> Transcript:
        if not sidecar_path:
            return transcript

        path = Path(sidecar_path)
        if not path.exists():
            raise FileNotFoundError(f"Speaker sidecar file not found: {path}")

        sidecar_data: Any = json.loads(path.read_text(encoding="utf-8"))

        word_map: dict[int, str] = {}

        # Case 1: Direct word-id mapping
        if isinstance(sidecar_data, dict) and "word_speakers" in sidecar_data:
            for wid_str, spk in sidecar_data["word_speakers"].items():
                try:
                    word_map[int(wid_str)] = str(spk)
                except ValueError:
                    continue

        # Case 2: Intervals list
        intervals: list[dict[str, Any]] = []
        if isinstance(sidecar_data, list):
            intervals = sidecar_data
        elif isinstance(sidecar_data, dict) and "intervals" in sidecar_data:
            intervals = sidecar_data["intervals"]

        new_words: list[Word] = []
        for w in transcript.words:
            assigned_speaker = w.speaker

            # Check direct word map first
            if w.id in word_map:
                assigned_speaker = word_map[w.id]
            else:
                # Check intervals by time overlap (midpoint of word)
                midpoint = (w.start + w.end) / 2.0
                for inv in intervals:
                    inv_start = float(inv.get("start", 0.0))
                    inv_end = float(inv.get("end", 0.0))
                    if inv_start <= midpoint <= inv_end:
                        assigned_speaker = str(inv.get("speaker", assigned_speaker))
                        break

            new_words.append(
                Word(
                    id=w.id,
                    text=w.text,
                    start=w.start,
                    end=w.end,
                    speaker=assigned_speaker,
                    confidence=w.confidence,
                )
            )

        return Transcript(
            words=new_words,
            title=transcript.title,
            source_duration=transcript.source_duration,
        )


SidecarDiarizationAdapter = MockSidecarDiarizationAdapter

