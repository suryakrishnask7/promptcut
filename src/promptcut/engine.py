"""
Engine: apply(log, transcript) -> Timeline

This is the ONLY place that knows how to combine the OperationLog with the
Transcript.  All logic is deterministic pure-Python: no I/O, no randomness,
no clock calls.

High-level algorithm
--------------------
1.  Collect active ops (index < pointer AND active == True).
2.  Walk ops in order, building mutable word-state tables:
      cut      : set[word_id]          – words removed from output
      speed    : dict[word_id, float]  – speed factor per word (last-write-wins)
      muted    : set[word_id]          – audio silenced
      bleeped  : set[word_id]          – audio bleeped
      word_order: list[word_id]        – current ordering (ReorderSections mutates this)
3.  Apply RemoveSilences config (does NOT cut words; compresses inter-word gaps
    at segment-build time).
4.  Build segments: split surviving words whenever speed/mute/bleep changes
    OR when consecutive words are not source-adjacent (i.e. there was a cut
    between them in the original transcript).
5.  Compute output timing for every word (needed for caption resolution).
6.  Resolve captions:
      • All anchor words cut  → remove caption, add warning.
      • Some anchor words cut → follow surviving words, enforce min-display.
      • No anchor words cut   → follow all words, enforce min-display.
7.  Compute timeline_hash from canonical JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from promptcut.config import CAPTION_MIN_DISPLAY_SECS, FLOAT_PRECISION
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption,
    AutoCaption,
    BleepWords,
    UnbleepWords,
    MaskCaptions,
    UnmaskCaptions,
    CutRange,
    KeepRange,
    MuteWords,
    RemoveFillers,
    RemoveSilences,
    ReorderSections,
    SetCaptionStyle,
    SpeedRange,
    ZoomSpeaker,
)
from promptcut.models.timeline import CaptionOverlay, Timeline, TimelineSegment, mask_word_text
from promptcut.models.transcript import Transcript, Word


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _CaptionPending:
    """Caption waiting to be resolved against the final word-time map."""

    word_ids: list[int]
    text: str
    min_display_secs: float
    source_op_id: int
    color: str = "white"
    font_size: int = 32
    position: str = "bottom"
    font_style: str = "normal"



def _normalise_word(text: str) -> str:
    """Lowercase and strip common punctuation for filler matching."""
    return text.lower().strip(".,!?;:\"'—–-")


def _round_f(v: float) -> float:
    """Round to FLOAT_PRECISION decimal places for determinism."""
    return round(v, FLOAT_PRECISION)


def _compute_hash(segments: list[TimelineSegment], captions: list[CaptionOverlay]) -> str:
    """
    Canonical sha256 of the timeline.

    Keys are sorted; floats are rounded to FLOAT_PRECISION.  No timestamps,
    UUIDs or any non-deterministic values enter the hash.
    """

    def _canonicalise(obj: object) -> object:
        if isinstance(obj, float):
            return _round_f(obj)
        if isinstance(obj, dict):
            return {k: _canonicalise(v) for k, v in sorted(obj.items())}
        if isinstance(obj, list):
            return [_canonicalise(i) for i in obj]
        return obj

    data = {
        "segments": [
            _canonicalise(s.model_dump(exclude={"duration"})) for s in segments
        ],
        "captions": [
            _canonicalise(c.model_dump(exclude={"duration"})) for c in captions
        ],
    }
    canonical_json = json.dumps(_canonicalise(data), sort_keys=True)
    return hashlib.sha256(canonical_json.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def apply(log: OperationLog, transcript: Transcript) -> Timeline:
    """
    Replay log over transcript and return the derived Timeline.

    Deterministic: same (log, transcript) → same Timeline on every call.
    """
    active_ops = log.active_ops  # ops[i] where i < pointer and active

    word_map: dict[int, Word] = transcript.word_map()
    orig_positions: dict[int, int] = transcript.original_positions()

    # -----------------------------------------------------------------
    # Step 1: initialise mutable word-state tables
    # -----------------------------------------------------------------
    word_order: list[int] = [w.id for w in transcript.words]
    cut: set[int] = set()
    speed: dict[int, float] = {}   # last-write-wins across all SpeedRange ops
    zoom: dict[int, float] = {}    # last-write-wins across all ZoomSpeaker ops
    muted: set[int] = set()
    bleeped: set[int] = set()
    unmasked_captions: set[int] = set()
    all_captions_unmasked: bool = False
    pending_captions: list[_CaptionPending] = []

    # RemoveSilences config (only the LAST RemoveSilences op takes effect)
    silence_cfg: RemoveSilences | None = None

    # Caption style overrides: target_op_id -> style dict, plus fallback for single caption
    caption_styles: dict[int, dict[str, Any]] = {}
    default_caption_style: dict[str, Any] = {}

    # -----------------------------------------------------------------
    # Step 2: walk ops
    # -----------------------------------------------------------------
    for op in active_ops:
        match op.op_type:
            case "cut_range":
                assert isinstance(op, CutRange)
                cut.update(op.word_ids)

            case "keep_range":
                assert isinstance(op, KeepRange)
                keep_set = set(op.word_ids)
                # Anything NOT in keep_set gets cut
                cut.update(wid for wid in word_order if wid not in keep_set)

            case "reorder_sections":
                assert isinstance(op, ReorderSections)
                # Flatten sections into new word order.
                # Words not listed in any section are excluded (see RULES.md §Reorder).
                new_order: list[int] = []
                for section in op.sections:
                    new_order.extend(section)
                word_order = new_order

            case "speed_range":
                assert isinstance(op, SpeedRange)
                # Last-write-wins per word
                for wid in op.word_ids:
                    speed[wid] = op.factor

            case "zoom_speaker":
                assert isinstance(op, ZoomSpeaker)
                # Last-write-wins per word
                for wid in op.word_ids:
                    zoom[wid] = op.factor

            case "mute_words":
                assert isinstance(op, MuteWords)
                muted.update(op.word_ids)

            case "bleep_words":
                assert isinstance(op, BleepWords)
                bleeped.update(op.word_ids)
                unmasked_captions.difference_update(op.word_ids)

            case "unbleep_words":
                assert isinstance(op, UnbleepWords)
                bleeped.difference_update(op.word_ids)
                unmasked_captions.difference_update(op.word_ids)

            case "unmask_captions":
                assert isinstance(op, UnmaskCaptions)
                if op.word_ids:
                    unmasked_captions.update(op.word_ids)
                else:
                    all_captions_unmasked = True
                    unmasked_captions.update(bleeped)

            case "mask_captions":
                assert isinstance(op, MaskCaptions)
                all_captions_unmasked = False
                if op.word_ids:
                    unmasked_captions.difference_update(op.word_ids)
                else:
                    unmasked_captions.clear()

            case "add_caption":
                assert isinstance(op, AddCaption)
                pending_captions.append(
                    _CaptionPending(
                        word_ids=list(op.word_ids),
                        text=op.text,
                        min_display_secs=op.min_display_secs,
                        source_op_id=op.op_id,
                        color=op.color,
                        font_size=getattr(op, "font_size", 32) or 32,
                        position=getattr(op, "position", "bottom") or "bottom",
                        font_style=getattr(op, "font_style", "normal") or "normal",
                    )
                )

            case "set_caption_style":
                assert isinstance(op, SetCaptionStyle)
                style_dict: dict[str, Any] = {}
                if op.color:
                    style_dict["color"] = op.color
                if op.font_size:
                    style_dict["font_size"] = op.font_size
                if op.position:
                    style_dict["position"] = op.position
                if op.font_style:
                    style_dict["font_style"] = op.font_style
                if op.text:
                    style_dict["text"] = op.text

                if style_dict:
                    if op.target_caption_op_id is not None:
                        caption_styles.setdefault(op.target_caption_op_id, {}).update(style_dict)
                    else:
                        default_caption_style.update(style_dict)


            case "auto_caption":
                assert isinstance(op, AutoCaption)
                # Derive text from word texts in source (id) order
                caption_words = sorted(
                    (word_map[wid] for wid in op.word_ids if wid in word_map),
                    key=lambda w: w.id,
                )
                text = " ".join(w.text for w in caption_words)
                pending_captions.append(
                    _CaptionPending(
                        word_ids=list(op.word_ids),
                        text=text,
                        min_display_secs=op.min_display_secs,
                        source_op_id=op.op_id,
                    )
                )

            case "remove_fillers":
                assert isinstance(op, RemoveFillers)
                filler_set = {_normalise_word(f) for f in op.filler_words}
                for wid in list(word_order):
                    if wid in word_map:
                        if _normalise_word(word_map[wid].text) in filler_set:
                            cut.add(wid)

            case "remove_silences":
                assert isinstance(op, RemoveSilences)
                # Last RemoveSilences op wins
                silence_cfg = op

            case "add_music_ducked":
                # Data-only in this phase; no timeline mutation
                pass

    if all_captions_unmasked:
        effective_masked: set[int] = set()
    else:
        effective_masked = {wid for wid in bleeped if wid not in unmasked_captions}

    # -----------------------------------------------------------------
    # Step 3: compute surviving word list
    # -----------------------------------------------------------------
    surviving: list[int] = [
        wid for wid in word_order if wid not in cut and wid in word_map
    ]

    warnings: list[str] = []

    if not surviving:
        # Empty timeline – caller (response layer) should have rejected this
        tl = Timeline(
            segments=[],
            captions=[],
            warnings=["All words were cut; timeline is empty."],
            total_duration=0.0,
            timeline_hash=_compute_hash([], []),
        )
        return tl

    # -----------------------------------------------------------------
    # Step 4: split surviving words into homogeneous segments
    #
    # Split boundary when ANY of these changes between consecutive words:
    #   a) speed factor
    #   b) muted flag
    #   c) bleeped flag
    #   d) words are not source-adjacent (gap due to cuts in-between)
    # -----------------------------------------------------------------

    def _seg_key(wid: int) -> tuple[float, bool, bool, float]:
        return (speed.get(wid, 1.0), wid in muted, wid in bleeped, zoom.get(wid, 1.0))

    def _are_source_adjacent(prev: int, curr: int) -> bool:
        """True iff curr immediately follows prev in the ORIGINAL transcript."""
        return orig_positions.get(curr, -1) == orig_positions.get(prev, -2) + 1

    raw_segments: list[list[int]] = []
    current_seg: list[int] = [surviving[0]]

    for i in range(1, len(surviving)):
        prev_wid = surviving[i - 1]
        curr_wid = surviving[i]
        if _seg_key(curr_wid) != _seg_key(prev_wid) or not _are_source_adjacent(
            prev_wid, curr_wid
        ):
            raw_segments.append(current_seg)
            current_seg = [curr_wid]
        else:
            current_seg.append(curr_wid)

    raw_segments.append(current_seg)

    # -----------------------------------------------------------------
    # Step 5: build TimelineSegments with output timing
    #
    # For each segment, source_duration = source_end - source_start
    # minus any silence removed by RemoveSilences.
    # output_duration = source_duration / speed_factor.
    # Segments are placed back-to-back (no gaps in the output).
    # -----------------------------------------------------------------

    # word_id → (output_start, output_end) – built as we process segments
    word_output_times: dict[int, tuple[float, float]] = {}

    timeline_segments: list[TimelineSegment] = []
    seg_counter = 0
    cursor = 0.0  # running output time

    for seg_wids in raw_segments:
        words_in_seg = [word_map[wid] for wid in seg_wids]
        spd = speed.get(seg_wids[0], 1.0)
        is_muted = seg_wids[0] in muted
        is_bleeped = seg_wids[0] in bleeped

        # --- Compute per-word output offsets within this segment ---
        # We build offset from the first word's source start.
        seg_src_start = words_in_seg[0].start
        offsets: list[tuple[float, float]] = []  # (src_offset_start, src_offset_end)

        cumulative_silence_removed = 0.0
        for j, word in enumerate(words_in_seg):
            # Gap between previous word's end and this word's start (in source)
            if j > 0 and silence_cfg is not None:
                gap = word.start - words_in_seg[j - 1].end
                if gap > silence_cfg.min_silence_secs:
                    removed = gap - silence_cfg.max_gap_secs
                    cumulative_silence_removed += removed

            src_offset_start = (word.start - seg_src_start) - cumulative_silence_removed
            src_offset_end = (word.end - seg_src_start) - cumulative_silence_removed
            offsets.append((src_offset_start, src_offset_end))

        # Total source duration after silence compression
        seg_src_duration = offsets[-1][1]  # end of last word's offset
        seg_out_duration = seg_src_duration / spd

        # Populate word_output_times
        for (src_s, src_e), wid in zip(offsets, seg_wids):
            out_s = _round_f(cursor + src_s / spd)
            out_e = _round_f(cursor + src_e / spd)
            word_output_times[wid] = (out_s, out_e)

        seg_start = _round_f(cursor)
        seg_end = _round_f(cursor + seg_out_duration)
        cursor += seg_out_duration

        zm = zoom.get(seg_wids[0], 1.0)
        timeline_segments.append(
            TimelineSegment(
                segment_id=seg_counter,
                source_word_ids=seg_wids,
                start_time=seg_start,
                end_time=seg_end,
                speed_factor=spd,
                is_muted=is_muted,
                is_bleeped=is_bleeped,
                speaker=words_in_seg[0].speaker,
                zoom_factor=zm,
            )
        )
        seg_counter += 1

    total_duration = _round_f(cursor)

    # -----------------------------------------------------------------
    # Step 6: resolve captions
    # -----------------------------------------------------------------
    resolved_captions: list[CaptionOverlay] = []
    removed_captions: list[CaptionOverlay] = []
    caption_counter = 0

    for cp in pending_captions:
        surviving_wids = [wid for wid in cp.word_ids if wid in word_output_times]

        # Determine caption styling
        eff_style: dict[str, Any] = {
            k: v for k, v in default_caption_style.items()
            if k != "text" or len(pending_captions) == 1
        }
        if cp.source_op_id in caption_styles:
            eff_style.update(caption_styles[cp.source_op_id])

        cap_color = eff_style.get("color", cp.color)
        cap_font_size = eff_style.get("font_size", cp.font_size)
        cap_position = eff_style.get("position", cp.position)
        cap_font_style = eff_style.get("font_style", cp.font_style)
        cap_text = eff_style.get("text", cp.text)

        if not surviving_wids:
            # All anchor words were cut → caption is removed
            # Build a dummy CaptionOverlay to put in removed_captions
            removed_captions.append(
                CaptionOverlay(
                    caption_id=caption_counter,
                    word_ids=cp.word_ids,
                    text=cap_text,
                    start_time=0.0,
                    end_time=0.0,
                    min_display_secs=cp.min_display_secs,
                    source_op_id=cp.source_op_id,
                    color=cap_color,
                    font_size=cap_font_size,
                    position=cap_position,
                    font_style=cap_font_style,
                )
            )
            warnings.append(
                f"Caption (op_id={cp.source_op_id}) was removed: "
                f"all {len(cp.word_ids)} anchor word(s) were cut."
            )
            caption_counter += 1
            continue

        if len(surviving_wids) < len(cp.word_ids):
            # Partial cut: warn, but continue with surviving words
            # (see RULES.md §PartialCaption)
            n_cut = len(cp.word_ids) - len(surviving_wids)
            warnings.append(
                f"Caption (op_id={cp.source_op_id}) partially cut: "
                f"{n_cut} of {len(cp.word_ids)} anchor word(s) removed; "
                "caption timing follows surviving words."
            )

        # Caption start = earliest output start of surviving anchor words
        # Caption end   = latest output end of surviving anchor words
        # Both extended to meet min_display_secs floor.
        starts = [word_output_times[wid][0] for wid in surviving_wids]
        ends = [word_output_times[wid][1] for wid in surviving_wids]

        cap_start = _round_f(min(starts))
        cap_end_raw = _round_f(max(ends))
        min_disp = max(cp.min_display_secs, CAPTION_MIN_DISPLAY_SECS)
        cap_end = _round_f(max(cap_end_raw, cap_start + min_disp))

        # Mask bleeped words in caption text if active
        final_cap_text = cap_text
        for wid in surviving_wids:
            if wid in effective_masked and wid in word_map:
                w_raw = word_map[wid].text
                pattern = re.compile(rf"\b{re.escape(w_raw)}\b", re.IGNORECASE)
                if pattern.search(final_cap_text):
                    final_cap_text = pattern.sub(lambda m: mask_word_text(m.group(0)), final_cap_text)
                elif w_raw.lower() in final_cap_text.lower():
                    pattern_sub = re.compile(re.escape(w_raw), re.IGNORECASE)
                    final_cap_text = pattern_sub.sub(lambda m: mask_word_text(m.group(0)), final_cap_text)

        resolved_captions.append(
            CaptionOverlay(
                caption_id=caption_counter,
                word_ids=surviving_wids,
                text=final_cap_text,
                start_time=cap_start,
                end_time=cap_end,
                min_display_secs=min_disp,
                source_op_id=cp.source_op_id,
                color=cap_color,
                font_size=cap_font_size,
                position=cap_position,
                font_style=cap_font_style,
            )
        )
        caption_counter += 1


    # -----------------------------------------------------------------
    # Step 7: compute timeline_hash
    # -----------------------------------------------------------------
    tl_hash = _compute_hash(timeline_segments, resolved_captions)

    return Timeline(
        segments=timeline_segments,
        captions=resolved_captions,
        removed_captions=removed_captions,
        warnings=warnings,
        total_duration=total_duration,
        timeline_hash=tl_hash,
        masked_word_ids=sorted(list(effective_masked)),
    )
