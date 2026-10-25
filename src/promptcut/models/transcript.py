"""
Transcript data models.

Design note: word IDs are sequential integers assigned at transcript creation
time and never change.  They are the stable anchor for every edit operation.
No edit ever modifies the Transcript – it is read-only source of truth.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Word(BaseModel):
    """
    A single transcribed word with its original timing in the source video.

    id        – monotonically increasing integer, 0-based, unique per transcript.
    text      – raw transcribed text (may include punctuation).
    start     – start time in seconds in the ORIGINAL source video.
    end       – end time in seconds in the ORIGINAL source video.
    speaker   – speaker label (e.g. "S1", "S2").
    confidence – ASR confidence score in [0, 1]; 1.0 for hand-written fixtures.
    """

    id: int
    text: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    speaker: str = "S1"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sentence_end: bool = False

    @model_validator(mode="after")
    def _end_after_start(self) -> "Word":
        if self.end < self.start:
            raise ValueError(
                f"Word {self.id!r}: end ({self.end}) must be >= start ({self.start})"
            )
        return self


class Transcript(BaseModel):
    """
    Immutable source-of-truth transcript.

    words          – ordered list of Words (must be sorted by start time,
                     ids must be unique and sequential starting from 0).
    title          – optional human-readable title for the video.
    source_duration – total duration of the original video in seconds.
                      If 0.0, it is inferred as the end time of the last word.
    """

    words: list[Word]
    title: str = ""
    source_duration: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _validate_words(self) -> "Transcript":
        ids = [w.id for w in self.words]
        if ids != list(range(len(ids))):
            raise ValueError("Word IDs must be sequential integers starting from 0")
        # Infer source_duration from last word if not supplied
        if self.source_duration == 0.0 and self.words:
            object.__setattr__(self, "source_duration", self.words[-1].end)
        return self

    # ------------------------------------------------------------------
    # Convenience helpers (pure, no side effects)
    # ------------------------------------------------------------------

    def word_by_id(self, word_id: int) -> Word | None:
        """O(1) lookup via list index (IDs are sequential from 0)."""
        if 0 <= word_id < len(self.words):
            w = self.words[word_id]
            # Sanity: id must equal the index (guaranteed by validator above)
            return w if w.id == word_id else None
        return None

    def word_map(self) -> dict[int, Word]:
        """Return {word_id: Word} mapping.  Rebuild on every call (no caching)
        to keep the model free of mutable state."""
        return {w.id: w for w in self.words}

    def original_positions(self) -> dict[int, int]:
        """Return {word_id: position_in_list}.  Used by the engine to detect
        whether consecutive surviving words are source-adjacent."""
        return {w.id: i for i, w in enumerate(self.words)}
