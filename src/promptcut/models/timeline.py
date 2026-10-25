"""
Timeline output models.

The Timeline is DERIVED – never stored as source of truth.  The engine
rebuilds it from the OperationLog + Transcript on every call to apply().

TimelineSegment  – a contiguous playback block of source words.
CaptionOverlay   – a text caption anchored to a set of source word IDs.
Timeline         – the complete output: segments + captions + hash.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimelineSegment(BaseModel):
    """
    One contiguous block of source content in the output video.

    segment_id      – sequential integer, 0-based in output order.
    source_word_ids – the word IDs (from the original Transcript) that this
                      segment plays back.  Always a contiguous run of source-
                      adjacent words with uniform speed / mute / bleep state.
    start_time      – start of this segment in the OUTPUT timeline (seconds).
    end_time        – end of this segment in the OUTPUT timeline (seconds).
    speed_factor    – how fast these words are played (>1 = faster).
    is_muted        – audio is silenced for this segment.
    is_bleeped      – audio is replaced with a bleep for this segment.
    speaker         – speaker label inherited from the first word in the segment.
    """

    segment_id: int
    source_word_ids: list[int]
    start_time: float
    end_time: float
    speed_factor: float = 1.0
    is_muted: bool = False
    is_bleeped: bool = False
    speaker: str = "S1"
    zoom_factor: float = 1.0

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


class CaptionOverlay(BaseModel):
    """
    A text caption displayed over a time range in the output video.

    caption_id    – sequential integer across the whole Timeline.
    word_ids      – the source word IDs this caption is anchored to.
                    These are the SURVIVING words after cuts; see RULES.md
                    §PartialCaption for what happens when some are cut.
    text          – the caption string (set by AddCaption; derived from
                    transcript text by AutoCaption).
    start_time    – when the caption first appears (OUTPUT timeline seconds).
    end_time      – when the caption disappears (≥ start_time + min_display_secs).
    min_display_secs – floor for caption display duration (default 0.7 s).
    source_op_id  – op_id of the AddCaption / AutoCaption that created this.
    """

    caption_id: int
    word_ids: list[int]
    text: str
    start_time: float
    end_time: float
    min_display_secs: float = 0.7
    source_op_id: int
    color: str = "white"
    font_size: int = 32
    position: str = "bottom"
    font_style: str = "normal"


    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


def mask_word_text(text: str) -> str:
    """
    Replace alphanumeric characters in the word with '*', keeping punctuation.
    The number of stars indicates the number of letters/digits in the word.
    E.g. 'wonder' -> '******', 'Hello!' -> '*****!'
    """
    if not text or not text.strip():
        return text
    masked = "".join("*" if ch.isalnum() else ch for ch in text)
    if not any(ch == "*" for ch in masked):
        masked = "".join("*" if not ch.isspace() else ch for ch in text)
    return masked


class Timeline(BaseModel):
    """
    The complete derived output of apply(log, transcript).

    segments          – ordered list of playback segments.
    captions          – caption overlays that survived all cuts.
    removed_captions  – captions whose EVERY anchor word was cut (reported as
                        warnings; see RULES.md §CaptionFullyCut).
    warnings          – human-readable messages about policy decisions made
                        during this replay (e.g. removed captions).
    total_duration    – sum of all segment durations (seconds).
    timeline_hash     – sha256 of canonical JSON (sorted keys, fixed float
                        rounding, no clock-derived values).  Identical for
                        identical (log, transcript) pairs on any machine.
    masked_word_ids   – list of word IDs currently masked in captions with asterisks.
    """

    segments: list[TimelineSegment] = Field(default_factory=list)
    captions: list[CaptionOverlay] = Field(default_factory=list)
    removed_captions: list[CaptionOverlay] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    total_duration: float = 0.0
    timeline_hash: str = ""
    masked_word_ids: list[int] = Field(default_factory=list)
