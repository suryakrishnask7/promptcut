"""
Advanced SubStation Alpha (.ass) Subtitle Generator.

Generates ASS subtitle scripts for burning caption overlays directly into video
frames during FFmpeg rendering, preserving styling, text colors, and positioning.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from promptcut.models.timeline import CaptionOverlay, mask_word_text

if TYPE_CHECKING:
    from promptcut.models.timeline import Timeline
    from promptcut.models.transcript import Transcript


def _format_ass_time(seconds: float) -> str:
    """Format seconds into H:MM:SS.cc for ASS dialogue events."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _color_to_ass(color_str: str) -> str:
    """
    Convert color name or RGB hex to ASS BGR format (&HBBGGRR&).
    """
    c = color_str.strip().lower()
    color_map = {
        "red": "&H0000FF&",
        "green": "&H00FF00&",
        "blue": "&HFF0000&",
        "yellow": "&H00FFFF&",
        "white": "&HFFFFFF&",
        "black": "&H000000&",
        "cyan": "&HFFFF00&",
        "magenta": "&HFF00FF&",
    }
    if c in color_map:
        return color_map[c]

    # If hex like #RRGGBB
    if c.startswith("#") and len(c) == 7:
        r, g, b = c[1:3], c[3:5], c[5:7]
        return f"&H{b}{g}{r}&"

    return "&HFFFFFF&"


def _compute_timeline_word_times(
    timeline: Timeline, transcript: Transcript
) -> dict[int, tuple[float, float, str]]:
    """
    Returns mapping: word_id -> (output_start, output_end, uppercase_text)
    """
    result: dict[int, tuple[float, float, str]] = {}
    masked_ids = set(getattr(timeline, "masked_word_ids", []))
    for seg in timeline.segments:
        if not seg.source_word_ids:
            continue
        first_w = transcript.word_by_id(seg.source_word_ids[0])
        seg_src_start = first_w.start if first_w else 0.0
        for wid in seg.source_word_ids:
            w = transcript.word_by_id(wid)
            if not w:
                continue
            src_offset_s = max(0.0, w.start - seg_src_start)
            src_offset_e = max(src_offset_s + 0.04, w.end - seg_src_start)
            out_s = seg.start_time + (src_offset_s / seg.speed_factor)
            out_e = seg.start_time + (src_offset_e / seg.speed_factor)
            w_text = w.text.strip()
            if wid in masked_ids:
                w_text = mask_word_text(w_text)
            result[wid] = (out_s, out_e, w_text.upper())
    return result


def generate_ass_subtitles(
    captions: list[CaptionOverlay],
    out_path: str | Path,
    play_res_x: int = 1280,
    play_res_y: int = 720,
    transcript: Transcript | None = None,
    timeline: Timeline | None = None,
) -> Path:
    """
    Generate an ASS subtitle file from a list of CaptionOverlay models with
    full uppercase text, thick black outline for visibility on any background,
    and word-by-word active highlighting.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    header = f"""[Script Info]
Title: PromptCut High-Visibility Captions
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    word_times: dict[int, tuple[float, float, str]] = {}
    if transcript is not None and timeline is not None:
        word_times = _compute_timeline_word_times(timeline, transcript)

    lines = [header]

    for cap in captions:
        pos = getattr(cap, "position", "bottom")
        if pos in ("middle", "center"):
            pos_tag = "\\an5"
        elif pos == "top":
            pos_tag = "\\an8"
        else:
            pos_tag = "\\an2"

        tags = [pos_tag]
        if getattr(cap, "font_size", None):
            tags.append(f"\\fs{cap.font_size}")

        fstyle = getattr(cap, "font_style", "normal")
        if fstyle == "italic":
            tags.append("\\i1")
        elif fstyle == "bold":
            tags.append("\\b1")

        base_tags = "".join(tags)

        # Highlight color for the currently active word
        # (Default to vibrant yellow &H00FFFF& if white/unspecified)
        if cap.color and cap.color.strip().lower() != "white":
            active_color = _color_to_ass(cap.color)
        else:
            active_color = "&H00FFFF&"  # Vibrant Yellow in BGR (&H00FFFF&)

        white_color = "&HFFFFFF&"

        # Check if we have word-level timeline output times for active karaoke slicing
        cap_words = [
            (wid, word_times[wid][0], word_times[wid][1], word_times[wid][2])
            for wid in cap.word_ids
            if wid in word_times
        ]
        cap_words.sort(key=lambda item: item[1])

        if cap_words and len(cap_words) >= 1:
            # Word-by-word active highlighting dialogue events
            # 1. Lead-in if caption appears before first word is spoken
            first_w_start = cap_words[0][1]
            if cap.start_time < first_w_start - 0.02:
                all_white_text = " ".join(f"{{\\c{white_color}}}{w[3]}" for w in cap_words)
                lines.append(
                    f"Dialogue: 0,{_format_ass_time(cap.start_time)},{_format_ass_time(first_w_start)},Default,,0,0,0,,{{{base_tags}}}{all_white_text}\n"
                )

            # 2. Time slices for each spoken word
            m = len(cap_words)
            for k in range(m):
                w_s = max(cap.start_time, cap_words[k][1])
                if k < m - 1:
                    w_e = min(cap.end_time, max(w_s + 0.05, cap_words[k + 1][1]))
                else:
                    w_e = max(w_s + 0.05, min(cap.end_time, cap_words[k][2]))

                # Active word has active_color, all others are white
                parts = []
                for j, (_, _, _, w_txt) in enumerate(cap_words):
                    if j == k:
                        parts.append(f"{{\\c{active_color}}}{w_txt}")
                    else:
                        parts.append(f"{{\\c{white_color}}}{w_txt}")
                line_text = " ".join(parts)
                lines.append(
                    f"Dialogue: 0,{_format_ass_time(w_s)},{_format_ass_time(w_e)},Default,,0,0,0,,{{{base_tags}}}{line_text}\n"
                )

            # 3. Hold if caption extends after last word is spoken (min_display floor)
            last_w_end = cap_words[-1][2]
            if last_w_end < cap.end_time - 0.02:
                all_white_text = " ".join(f"{{\\c{white_color}}}{w[3]}" for w in cap_words)
                lines.append(
                    f"Dialogue: 0,{_format_ass_time(last_w_end)},{_format_ass_time(cap.end_time)},Default,,0,0,0,,{{{base_tags}}}{all_white_text}\n"
                )
        else:
            # Fallback when transcript or word timings are absent
            start_str = _format_ass_time(cap.start_time)
            end_str = _format_ass_time(cap.end_time)
            upper_text = cap.text.upper()
            line_text = f"{{{base_tags}\\c{active_color}}}{upper_text}"
            lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line_text}\n")

    path.write_text("".join(lines), encoding="utf-8")
    return path

