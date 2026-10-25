"""
Silence Snapping (Anti-Clipping Protection).

Snaps user cut boundaries to the nearest natural silence gap between words,
preventing speech from being clipped mid-syllable. Emits warnings if a cut
boundary falls mid-word without an accessible silence.
"""

from __future__ import annotations

from dataclasses import dataclass
from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript, Word


@dataclass
class SnappedCut:
    """Result of snapping a requested time range to silence boundaries."""

    snapped_start_s: float
    snapped_end_s: float
    target_word_ids: list[int]
    warnings: list[str]


def snap_cut_to_silence(
    transcript: Transcript,
    timeline: Timeline,
    start_s: float,
    end_s: float,
    tolerance_s: float = 0.4,
    min_silence_gap: float = 0.15,
) -> SnappedCut:
    """
    Snap requested [start_s, end_s] in output timeline coordinates to clean silence gaps.

    Parameters
    ----------
    transcript : Transcript
        Source transcript words.
    timeline : Timeline
        Current output timeline (per TIME_POLICY == 'current').
    start_s : float
        Requested start time of the cut.
    end_s : float
        Requested end time of the cut.
    tolerance_s : float
        Maximum distance to shift a cut boundary to reach a silence gap.
    min_silence_gap : float
        Minimum gap duration between words to qualify as clean silence.

    Returns
    -------
    SnappedCut
        The adjusted start/end timestamps, words to cut, and any clipping warnings.
    """
    warnings: list[str] = []

    # Map surviving words to their current output timeline boundaries
    # Segments in timeline define output placement
    word_times: dict[int, tuple[float, float, Word]] = {}
    for seg in timeline.segments:
        words_in_seg = [w for w in transcript.words if w.id in seg.source_word_ids]
        if not words_in_seg:
            continue
        src_total = words_in_seg[-1].end - words_in_seg[0].start
        seg_dur = seg.end_time - seg.start_time
        scale = seg_dur / src_total if src_total > 0 else 1.0
        src_base = words_in_seg[0].start

        for w in words_in_seg:
            out_s = seg.start_time + (w.start - src_base) * scale
            out_e = seg.start_time + (w.end - src_base) * scale
            word_times[w.id] = (round(out_s, 3), round(out_e, 3), w)

    sorted_words = sorted(word_times.values(), key=lambda item: item[0])

    def _snap_point(t: float, is_start_boundary: bool) -> tuple[float, list[str]]:
        point_warnings: list[str] = []
        if not sorted_words:
            return t, point_warnings

        # Check if t falls inside any word
        inside_word: Word | None = None
        for ws, we, word in sorted_words:
            if ws < t < we:
                inside_word = word
                break

        if not inside_word:
            # Already in a gap or outside word bounds
            return t, point_warnings

        # Word is intersected! Look for silence before and after
        ws_out = next(ws for ws, we, w in sorted_words if w.id == inside_word.id)
        we_out = next(we for ws, we, w in sorted_words if w.id == inside_word.id)

        dist_to_start = abs(t - ws_out)
        dist_to_end = abs(t - we_out)

        # Decide whether to snap to word start (before) or word end (after)
        if is_start_boundary:
            # For start of cut: prefer snapping to start of the word if close, or end
            if dist_to_start <= tolerance_s:
                return ws_out, point_warnings
            elif dist_to_end <= tolerance_s:
                return we_out, point_warnings
        else:
            # For end of cut: prefer snapping to end of the word if close, or start
            if dist_to_end <= tolerance_s:
                return we_out, point_warnings
            elif dist_to_start <= tolerance_s:
                return ws_out, point_warnings

        # Cannot cleanly snap within tolerance: emit warning
        boundary_name = "Start" if is_start_boundary else "End"
        point_warnings.append(
            f"{boundary_name} of cut ({t:.2f}s) falls mid-word on '{inside_word.text}' "
            f"({ws_out:.2f}s–{we_out:.2f}s); could not snap to silence within {tolerance_s:.2f}s tolerance. "
            "Audio may be clipped."
        )
        return t, point_warnings

    snapped_start, w_start = _snap_point(start_s, is_start_boundary=True)
    snapped_end, w_end = _snap_point(end_s, is_start_boundary=False)
    warnings.extend(w_start)
    warnings.extend(w_end)

    # Find words whose timing overlaps the snapped range
    target_ids = [
        word.id
        for ws, we, word in sorted_words
        if ws < snapped_end and we > snapped_start
    ]

    return SnappedCut(
        snapped_start_s=round(snapped_start, 3),
        snapped_end_s=round(snapped_end, 3),
        target_word_ids=target_ids,
        warnings=warnings,
    )
