"""
All edit operation types, plus the discriminated-union alias ``Operation``.

Design principles:
- Every op anchors to WORD IDs, never to raw timestamps.
- op_id defaults to -1 (unassigned); OperationLog.push() assigns the real id.
- ``active`` is toggled by restore_op(); undo/redo move the log pointer instead.
- The discriminator field ``op_type`` is a Literal so Pydantic can deserialise
  a bare dict back to the correct subclass without extra configuration.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator


class _BaseOp(BaseModel):
    """
    Fields shared by every operation.

    op_id   – monotonically increasing integer assigned by OperationLog.push().
              -1 is the sentinel for "not yet pushed".
    active  – False when restore_op() has toggled this op off.  Undo/redo do
              NOT touch this flag; they move the log pointer instead.
    """

    op_id: int = -1
    active: bool = True
    interpreted_by: str | None = None


# ---------------------------------------------------------------------------
# Structural edits (affect which words appear in the timeline)
# ---------------------------------------------------------------------------


class CutRange(_BaseOp):
    """Remove the listed words from the timeline entirely."""

    op_type: Literal["cut_range"] = "cut_range"
    word_ids: list[int] = Field(min_length=1)


class KeepRange(_BaseOp):
    """Keep ONLY the listed words; everything else is cut."""

    op_type: Literal["keep_range"] = "keep_range"
    word_ids: list[int] = Field(min_length=1)


class ReorderSections(_BaseOp):
    """
    Lay out the timeline as an ordered list of word-ID sections.

    Each inner list is a contiguous block of word IDs that will appear in the
    output in the order listed.  Words absent from every section are excluded
    from the output (see RULES.md §Reorder).
    """

    op_type: Literal["reorder_sections"] = "reorder_sections"
    sections: list[list[int]] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Audio edits
# ---------------------------------------------------------------------------


class SpeedRange(_BaseOp):
    """
    Apply a speed multiplier to the listed words.

    factor > 1.0  → faster (shorter duration in output).
    factor < 1.0  → slower (longer duration).
    factor == 1.0 → no change.

    When two SpeedRange ops target the same word the LAST one in the log wins
    (last-write-wins; see RULES.md §OverlappingSpeed).
    """

    op_type: Literal["speed_range"] = "speed_range"
    word_ids: list[int] = Field(min_length=1)
    factor: float = Field(gt=0.0)


class MuteWords(_BaseOp):
    """Silence the audio for the listed words (video frame retained)."""

    op_type: Literal["mute_words"] = "mute_words"
    word_ids: list[int] = Field(min_length=1)


class BleepWords(_BaseOp):
    """Replace the audio for the listed words with a bleep tone."""

    op_type: Literal["bleep_words"] = "bleep_words"
    word_ids: list[int] = Field(min_length=1)


class UnbleepWords(_BaseOp):
    """Remove audio bleep tone from the listed words, restoring original audio."""

    op_type: Literal["unbleep_words"] = "unbleep_words"
    word_ids: list[int] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Caption overlays
# ---------------------------------------------------------------------------


class AddCaption(_BaseOp):
    """
    Attach an explicit text caption anchored to the listed words.

    The caption is displayed for the full duration that any of its anchor words
    are on screen, extended to at least ``min_display_secs`` (see RULES.md).
    """

    op_type: Literal["add_caption"] = "add_caption"
    word_ids: list[int] = Field(min_length=1)
    text: str
    min_display_secs: float = Field(default=0.7, gt=0.0)
    color: str = "white"
    font_size: int = 32
    position: str = "bottom"
    font_style: str = "normal"


class SetCaptionStyle(_BaseOp):
    """
    Update visual style attributes (color, font_size, position, font_style, text) of a caption.

    If target_caption_op_id is None and exactly one caption exists, applies to it.
    """

    op_type: Literal["set_caption_style"] = "set_caption_style"
    target_caption_op_id: int | None = None
    color: str | None = None
    font_size: int | None = None
    position: str | None = None
    font_style: str | None = None
    text: str | None = None



class AutoCaption(_BaseOp):
    """
    Auto-generate a caption from the transcript text of the listed words.

    The engine joins the word texts in source order to form the caption string.
    """

    op_type: Literal["auto_caption"] = "auto_caption"
    word_ids: list[int] = Field(min_length=1)
    min_display_secs: float = Field(default=0.7, gt=0.0)
    color: str = "white"


# ---------------------------------------------------------------------------
# Cleanup passes
# ---------------------------------------------------------------------------


class RemoveFillers(_BaseOp):
    """
    Cut all words whose normalised text matches any entry in ``filler_words``.

    Normalisation: lowercase, strip leading/trailing punctuation.
    Common defaults: ["um", "uh", "like"].
    """

    op_type: Literal["remove_fillers"] = "remove_fillers"
    filler_words: list[str] = Field(min_length=1)


class RemoveSilences(_BaseOp):
    """
    Compress inter-word gaps that exceed ``min_silence_secs`` down to
    ``max_gap_secs`` in the output timeline.

    Words are NOT cut; only the gaps between them are shortened.
    Applied at segment-build time in the engine (see engine.py).
    """

    op_type: Literal["remove_silences"] = "remove_silences"
    min_silence_secs: float = Field(default=0.5, gt=0.0)
    max_gap_secs: float = Field(default=0.3, ge=0.0)


# ---------------------------------------------------------------------------
# Music / ducking (data-only in this phase)
# ---------------------------------------------------------------------------


class AddMusicDucked(_BaseOp):
    """
    Overlay a music track, ducking it under speech for the listed words.

    ``track_ref`` is an opaque reference to a music asset (resolved later by
    the ffmpeg layer, not in scope for this phase).
    ``duck_level_db`` is how many dB to attenuate the music under speech.
    """

    op_type: Literal["add_music_ducked"] = "add_music_ducked"
    word_ids: list[int] = Field(min_length=1)
    track_ref: str
    duck_level_db: float = Field(default=-18.0, le=0.0)


class MaskCaptions(_BaseOp):
    """
    Mask bleeped words in captions with asterisks matching their letter count.
    If word_ids is empty, masks all bleeped words.
    """

    op_type: Literal["mask_captions"] = "mask_captions"
    word_ids: list[int] = Field(default_factory=list)

    @field_validator("word_ids", mode="before")
    @classmethod
    def _coerce_word_ids(cls, v: Any) -> list[int]:
        if v is None:
            return []
        return v


class UnmaskCaptions(_BaseOp):
    """
    Unmask bleeped words in captions, revealing the original word text.
    If word_ids is empty, unmasks all bleeped words.
    """

    op_type: Literal["unmask_captions"] = "unmask_captions"
    word_ids: list[int] = Field(default_factory=list)

    @field_validator("word_ids", mode="before")
    @classmethod
    def _coerce_word_ids(cls, v: Any) -> list[int]:
        if v is None:
            return []
        return v


class ZoomSpeaker(_BaseOp):
    """
    Apply a visual zoom / punch-in effect to the video during the listed words.

    factor: zoom magnification factor (>1.0 = punch in / closer framing, e.g. 1.2 = 20% zoom).
    anchor: visual framing anchor point ("center", "top").
    """

    op_type: Literal["zoom_speaker"] = "zoom_speaker"
    word_ids: list[int] = Field(min_length=1)
    factor: float = Field(default=1.2, gt=0.5, le=3.0)
    anchor: Literal["center", "top"] = "center"


# ---------------------------------------------------------------------------
# Discriminated union – use this type annotation everywhere
# ---------------------------------------------------------------------------

Operation = Annotated[
    Union[
        CutRange,
        KeepRange,
        ReorderSections,
        SpeedRange,
        ZoomSpeaker,
        MuteWords,
        BleepWords,
        UnbleepWords,
        AddCaption,
        SetCaptionStyle,
        AutoCaption,
        MaskCaptions,
        UnmaskCaptions,
        RemoveFillers,
        RemoveSilences,
        AddMusicDucked,
    ],
    Field(discriminator="op_type"),
]

