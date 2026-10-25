"""
OpenTimelineIO (OTIO) Export Converter for PromptCut (promptcut).

Serializes a promptcut Timeline and Transcript into a standard OpenTimelineIO
JSON structure (OTIO_SCHEMA: "Timeline.1") with Video, Audio, and Caption/Marker tracks.
Enables interoperability with NLEs (DaVinci Resolve, Final Cut Pro, Premiere)
and fulfills the Outstanding Level export requirement in the IncuBrix brief.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript


def to_otio_dict(
    timeline: Timeline,
    transcript: Transcript,
    fps: float = 24.0,
    project_name: str = "PromptCut Edited Video",
) -> dict[str, Any]:
    """
    Convert a promptcut Timeline into an OpenTimelineIO 1.0 JSON-compatible dict.

    Parameters
    ----------
    timeline : Timeline
        Derived timeline from engine.apply().
    transcript : Transcript
        Source transcript providing word and speaker context.
    fps : float
        Nominal frame rate for RationalTime representations (default: 24.0).
    project_name : str
        Human-readable timeline name.

    Returns
    -------
    dict[str, Any]
        Standard OTIO Timeline.1 representation.
    """

    def _rational_time(seconds: float) -> dict[str, Any]:
        return {
            "OTIO_SCHEMA": "RationalTime.1",
            "rate": float(fps),
            "value": round(seconds * fps, 4),
        }

    def _time_range(start_s: float, duration_s: float) -> dict[str, Any]:
        return {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": _rational_time(start_s),
            "duration": _rational_time(duration_s),
        }

    video_clips: list[dict[str, Any]] = []
    audio_clips: list[dict[str, Any]] = []

    for seg in timeline.segments:
        seg_dur = seg.end_time - seg.start_time
        if seg_dur <= 0:
            continue

        words = [
            w for wid in seg.source_word_ids
            if (w := transcript.word_by_id(wid)) is not None
        ]
        src_start = words[0].start if words else seg.start_time
        src_end = words[-1].end if words else seg.end_time
        src_dur = max(0.01, src_end - src_start)

        # Video clip representation
        v_clip = {
            "OTIO_SCHEMA": "Clip.1",
            "name": f"Segment_{seg.segment_id} ({seg.speaker})",
            "metadata": {
                "promptcut": {
                    "segment_id": seg.segment_id,
                    "speaker": seg.speaker,
                    "speed_factor": seg.speed_factor,
                    "zoom_factor": getattr(seg, "zoom_factor", 1.0),
                    "word_count": len(seg.source_word_ids),
                }
            },
            "source_range": _time_range(src_start, src_dur),
        }
        video_clips.append(v_clip)

        # Audio clip representation (handles muted / bleeped state)
        if seg.is_muted:
            a_item: dict[str, Any] = {
                "OTIO_SCHEMA": "Gap.1",
                "name": f"Muted_Segment_{seg.segment_id}",
                "source_range": _time_range(0.0, seg_dur),
            }
        else:
            a_item = {
                "OTIO_SCHEMA": "Clip.1",
                "name": f"Audio_Segment_{seg.segment_id}" + (" [BLEEPED]" if seg.is_bleeped else ""),
                "metadata": {
                    "promptcut": {
                        "is_bleeped": seg.is_bleeped,
                        "speed_factor": seg.speed_factor,
                    }
                },
                "source_range": _time_range(src_start, src_dur),
            }
        audio_clips.append(a_item)

    # Markers for Captions
    markers: list[dict[str, Any]] = []
    for cap in timeline.captions:
        marker = {
            "OTIO_SCHEMA": "Marker.1",
            "name": cap.text,
            "color": cap.color.upper() if cap.color.upper() in ("RED", "GREEN", "BLUE", "YELLOW") else "WHITE",
            "marked_range": _time_range(cap.start_time, cap.end_time - cap.start_time),
            "metadata": {
                "promptcut": {
                    "caption_id": cap.caption_id,
                    "source_op_id": cap.source_op_id,
                    "word_ids": cap.word_ids,
                    "color": cap.color,
                }
            },
        }
        markers.append(marker)

    otio_doc: dict[str, Any] = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": project_name,
        "metadata": {
            "promptcut": {
                "timeline_hash": timeline.timeline_hash,
                "total_duration": timeline.total_duration,
                "segment_count": len(timeline.segments),
                "caption_count": len(timeline.captions),
            }
        },
        "global_start_time": None,
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "metadata": {},
            "children": [
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": "Video Track",
                    "kind": "Video",
                    "metadata": {},
                    "children": video_clips,
                    "markers": markers,
                },
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": "Audio Track",
                    "kind": "Audio",
                    "metadata": {},
                    "children": audio_clips,
                },
            ],
        },
    }

    return otio_doc


def export_otio(
    timeline: Timeline,
    transcript: Transcript,
    out_path: str | Path,
    fps: float = 24.0,
    project_name: str = "PromptCut Edited Video",
) -> Path:
    """
    Export a promptcut Timeline and Transcript to an OpenTimelineIO (.otio) file.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = to_otio_dict(timeline, transcript, fps=fps, project_name=project_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return path
