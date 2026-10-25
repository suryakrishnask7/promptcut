"""
Resolver: text / time expressions → word-ID ranges.

Three resolver modes
--------------------
1. phrase_search(transcript, phrase)
   Exact / phrase match over the transcript.  Returns ALL occurrences so the
   caller can decide whether the result is unambiguous (1 match → apply),
   ambiguous (>1 → NeedsClarification), or absent (0 → Rejected).

2. parse_time_expression(expr) → float (seconds)
   Parses human time strings into seconds.  Supports:
     "80 seconds", "1 minute 20 seconds", "a minute twenty",
     "1:20", "01:20", "1:20:05"
   "the last N seconds" → parsed as a negative offset (caller resolves
   against total_duration).

3. time_range_to_word_ids(transcript, timeline, start_s, end_s)
   Given a time range in OUTPUT coordinates (per TIME_POLICY), find all
   word IDs whose output timing overlaps [start_s, end_s].

4. MeaningSearchAdapter (abstract) + MockMeaningSearchAdapter
   Future hook for LLM / embedding semantic search.  The mock returns [].
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from promptcut.config import TIME_HINT_TOLERANCE_SECS
from promptcut.interpreter.base import IntentTarget, InterpretationResult
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption,
    BleepWords,
    CutRange,
    KeepRange,
    MaskCaptions,
    MuteWords,
    Operation,
    RemoveFillers,
    RemoveSilences,
    SetCaptionStyle,
    SpeedRange,
    UnbleepWords,
    UnmaskCaptions,
    ZoomSpeaker,
)
from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript
from promptcut.render.caption_chunker import chunk_transcript_for_captions


# ---------------------------------------------------------------------------
# 1. Phrase search
# ---------------------------------------------------------------------------


def phrase_search(transcript: Transcript, phrase: str) -> list[list[int]]:
    """
    Find all occurrences of ``phrase`` in the transcript by exact word match.

    Normalisation: lowercase, strip leading/trailing punctuation per word.
    Returns a list of word-ID lists – one entry per occurrence.

    Examples
    --------
    phrase_search(t, "pricing")        → [[42], [88], [115]]   (3 hits)
    phrase_search(t, "hello world")    → [[0, 1]]              (1 hit)
    phrase_search(t, "nonexistent")    → []
    """
    words = transcript.words
    phrase_tokens = [_norm(tok) for tok in phrase.split()]
    n = len(phrase_tokens)
    if n == 0:
        return []

    matches: list[list[int]] = []
    for i in range(len(words) - n + 1):
        chunk = [_norm(words[i + j].text) for j in range(n)]
        if chunk == phrase_tokens:
            matches.append([words[i + j].id for j in range(n)])
    return matches


def _norm(text: str) -> str:
    """Lowercase and strip common punctuation for matching."""
    return text.lower().strip(".,!?;:\"'—–-()[]")


def fuzzy_phrase_search(
    transcript: Transcript,
    phrase: str,
    threshold: float = 0.5,
) -> list[list[int]]:
    """
    Fuzzy phrase search that finds occurrences even if minor words or punctuation differ.
    Returns matched word ID lists.
    """
    words = transcript.words
    phrase_tokens = [_norm(tok) for tok in phrase.split() if _norm(tok)]
    n = len(phrase_tokens)
    if n == 0 or not words:
        return []

    norm_words = [_norm(w.text) for w in words]
    best_matches: list[list[int]] = []
    best_score = 0.0

    # Test candidate windows from n-1 to n+2
    for win_len in range(max(1, n - 1), min(len(words) + 1, n + 3)):
        for i in range(len(words) - win_len + 1):
            window = norm_words[i : i + win_len]
            matched_count = sum(1 for pt in phrase_tokens if pt in window)
            score = matched_count / max(len(phrase_tokens), len(window))
            if score >= threshold:
                if score > best_score:
                    best_score = score
                    best_matches = [[words[i + j].id for j in range(win_len)]]
                elif score == best_score:
                    candidate = [words[i + j].id for j in range(win_len)]
                    if candidate not in best_matches:
                        best_matches.append(candidate)

    return best_matches


# ---------------------------------------------------------------------------
# 2. Time expression parser
# ---------------------------------------------------------------------------


# Named-number words used in spoken time expressions
_WORD_TO_NUM: dict[str, float] = {
    "zero": 0, "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}

_UNIT_TO_SECS: dict[str, float] = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
}


def _word_to_number(token: str) -> float | None:
    return _WORD_TO_NUM.get(token.lower())


def parse_time_expression(expr: str) -> float:
    """
    Parse a human time expression and return seconds (float).

    Supported formats
    -----------------
    • "80 seconds" / "80s"
    • "1:20"  / "01:20"  / "1:20:05"
    • "a minute twenty"  / "one minute twenty seconds"
    • "1 minute 20"  /  "1 min 20 sec"
    • Plain integer / float: "90" → 90.0

    Raises ValueError for unrecognised expressions.

    Note: "the last N seconds" is NOT handled here – the caller must strip
    "the last" / "the final" prefix and use the returned value as a negative
    offset from total_duration.
    """
    # --- Colon format: MM:SS or HH:MM:SS ---
    if ":" in expr:
        parts = expr.split(":")
        try:
            if len(parts) == 2:
                m, s = float(parts[0]), float(parts[1])
                return m * 60.0 + s
            elif len(parts) == 3:
                h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                return h * 3600.0 + m * 60.0 + s
        except ValueError:
            pass

    # --- Plain number or number with trailing 's' ---
    plain = re.fullmatch(r"(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?", expr)
    if plain:
        return float(plain.group(1))
    try:
        return float(expr)
    except ValueError:
        pass

    # --- Mixed word / number expression ---
    tokens = expr.split()
    total = 0.0
    matched_any = False
    last_unit_multiplier: float | None = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        num: float | None = None
        try:
            num = float(tok)
        except ValueError:
            num = _word_to_number(tok)

        if num is not None:
            # Check if next token is a unit
            if i + 1 < len(tokens):
                unit = tokens[i + 1]
                mult = _UNIT_TO_SECS.get(unit)
                if mult is not None:
                    total += num * mult
                    matched_any = True
                    last_unit_multiplier = mult
                    i += 2
                    continue
            # If no explicit unit, check if this could be seconds following minutes
            if matched_any and last_unit_multiplier == 60:
                total += num  # default to seconds
                last_unit_multiplier = 1
                i += 1
                continue

        i += 1

    if matched_any:
        return total

    raise ValueError(f"Cannot parse time expression: {expr!r}")


# ---------------------------------------------------------------------------
# 3. Time range → word IDs (uses OUTPUT timeline coordinates)
# ---------------------------------------------------------------------------


def get_timeline_word_times(
    transcript: Transcript,
    timeline: Timeline,
) -> dict[int, tuple[float, float]]:
    """
    Build a word_id -> (output_start_s, output_end_s) map for all words currently
    present in the active timeline segments.
    """
    word_times: dict[int, tuple[float, float]] = {}
    for seg in timeline.segments:
        words_in_seg = [
            w for w in transcript.words if w.id in seg.source_word_ids
        ]
        if not words_in_seg:
            continue
        src_total = words_in_seg[-1].end - words_in_seg[0].start
        if src_total <= 0:
            continue
        seg_dur = seg.end_time - seg.start_time
        scale = seg_dur / src_total

        src_base = words_in_seg[0].start
        for w in words_in_seg:
            wout_s = seg.start_time + (w.start - src_base) * scale
            wout_e = seg.start_time + (w.end - src_base) * scale
            word_times[w.id] = (wout_s, wout_e)
    return word_times


def time_range_to_word_ids(
    transcript: Transcript,
    timeline: Timeline,
    start_s: float,
    end_s: float,
) -> list[int]:
    """
    Return word IDs whose output-timeline window overlaps [start_s, end_s].

    Per TIME_POLICY == "current", start_s / end_s are in the CURRENT edited
    timeline, not the original source.
    """
    word_times = get_timeline_word_times(transcript, timeline)
    result = [
        wid
        for wid, (ws, we) in word_times.items()
        if ws < end_s and we > start_s
    ]
    result.sort(key=lambda wid: word_times[wid][0])
    return result


# ---------------------------------------------------------------------------
# 4. Meaning search adapter (interface + mock)
# ---------------------------------------------------------------------------


class MeaningSearchAdapter(ABC):
    """
    Abstract adapter for semantic / meaning-based search over a transcript.

    An LLM or embedding model will implement this in a later phase.  The engine
    core never calls it directly – the response layer (response.py) does.
    """

    @abstractmethod
    def search(self, transcript: Transcript, query: str) -> list[list[int]]:
        """
        Return a list of word-ID ranges whose meaning matches ``query``.

        Like phrase_search, multiple matches → NeedsClarification.
        An empty list means no semantic match was found.
        """
        ...


class MockMeaningSearchAdapter(MeaningSearchAdapter):
    """
    Placeholder that always returns no semantic matches.

    Replaced by a real implementation (embedding model or LLM) in a later phase.
    """

    def search(self, transcript: Transcript, query: str) -> list[list[int]]:
        # Intentionally returns empty so the response layer falls through to
        # exact phrase search only.
        return []


# ---------------------------------------------------------------------------
# 5. Intent resolution
# ---------------------------------------------------------------------------


def resolve_intent_target(
    target: IntentTarget,
    transcript: Transcript,
    timeline: Timeline,
    log: OperationLog,
    pending_clarification: dict[str, Any] | None = None,
) -> tuple[str, list[Operation] | None, dict[str, Any]]:
    """
    Resolve an IntentTarget into engine operations against Transcript and Timeline.

    Applies TIME_HINT_TOLERANCE_SECS and deterministic tie-breaking:
    if two candidate occurrences are equidistant from target.time_hint, the
    earlier occurrence in timeline order wins.
    """
    action = target.action.lower()

    if action == "undo":
        return "undo", [], {"summary": "Reversed last instruction."}

    if action == "redo":
        return "redo", [], {"summary": "Re-applied last undone instruction."}

    if action == "render":
        return "render", [], {"summary": "Render request received."}

    if action == "restore":
        target_op_id = getattr(target, "target_op_id", None)
        if target_op_id is None:
            if isinstance(target.occurrence, int):
                target_op_id = target.occurrence
            elif isinstance(target.occurrence, str) and target.occurrence.isdigit():
                target_op_id = int(target.occurrence)
            elif target.time_hint is not None:
                target_op_id = int(target.time_hint)

        if target_op_id is None:
            for op in reversed(log.active_ops):
                if op.op_type == "cut_range":
                    if 0 in getattr(op, "word_ids", []):
                        target_op_id = op.op_id
                        break
            if target_op_id is None:
                for op in reversed(log.active_ops):
                    if op.op_type == "cut_range":
                        target_op_id = op.op_id
                        break
        if target_op_id is not None:
            return "restore", [], {"target_op_id": target_op_id, "summary": f"Restored operation #{target_op_id}."}
        return "rejected", None, {"reason": "No matching cut found to restore."}

    if action == "remove_fillers":
        return "applied", [RemoveFillers(filler_words=["um", "uh", "like"])], {"summary": "Removed filler words (um, uh, like)."}

    if action == "remove_silences":
        return "applied", [RemoveSilences()], {"summary": "Removed silences."}

    if action == "set_default_font_step":
        step = int(target.factor or 4)
        return "applied", [], {"step": step, "summary": f"Updated default font size increase/decrease step to {step} pt."}

    if action == "set_caption_style":
        target_id: int | None = None
        if isinstance(target.occurrence, int):
            target_id = target.occurrence
        elif isinstance(target.occurrence, str) and target.occurrence.isdigit():
            target_id = int(target.occurrence)

        final_font_size = target.font_size
        if final_font_size is None and target.font_size_delta is not None:
            cur_size = 32
            if timeline and timeline.captions:
                cur_size = getattr(timeline.captions[0], "font_size", 32) or 32
            final_font_size = max(10, cur_size + target.font_size_delta)

        op = SetCaptionStyle(
            target_caption_op_id=target_id,
            font_size=final_font_size,
            font_style=target.font_style,  # type: ignore
            color=target.color,
            position=target.position,  # type: ignore
            text=target.text.upper() if target.text else None,
        )
        if target.font_size_delta is not None:
            dir_str = "Increased" if target.font_size_delta > 0 else "Decreased"
            summary = f"{dir_str} caption font size by {target.font_size_delta:+d} pt to {final_font_size} pt."
        elif target_id is not None:
            summary = f"Updated caption #{target_id}."
        else:
            summary = "Updated caption style."
        return "applied", [op], {"summary": summary}

    if action == "clarification_answer":
        if pending_clarification is None:
            return "rejected", None, {"reason": "No pending clarification to answer."}

        p_act = pending_clarification.get("action")
        p_q = str(pending_clarification.get("question", "")).lower()

        # Handle font size clarification
        if p_act == "font_size" or "font" in p_q or "size" in p_q:
            ans_str = str(target.occurrence or target.text or "").strip()
            m_val = re.search(r"([+-]?\d+)", ans_str)
            cur_size = 32
            if timeline and timeline.captions:
                cur_size = getattr(timeline.captions[0], "font_size", 32) or 32
            if m_val:
                val = int(m_val.group(1))
                if "+" in ans_str or "-" in ans_str or "increase" in ans_str.lower() or "decrease" in ans_str.lower():
                    new_size = max(10, cur_size + val)
                else:
                    new_size = val
            else:
                new_size = cur_size + 4

            op = SetCaptionStyle(font_size=new_size)
            return "applied", [op], {"summary": f"Set caption font size to {new_size} pt."}

        candidate_hits: list[list[int]] = pending_clarification.get("candidate_hits", [])
        if not candidate_hits:
            phrase = pending_clarification.get("phrase")
            if not phrase:
                q_text = str(pending_clarification.get("question", ""))
                m_q = re.search(r"['\"]([^'\"]+)['\"]", q_text)
                if m_q:
                    phrase = m_q.group(1).strip()
            if phrase:
                candidate_hits = phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)

        if not candidate_hits:
            opts = pending_clarification.get("options", [])
            for opt in opts:
                m_t = re.search(r"~?(\d+(?:\.\d+)?)s", opt)
                if m_t:
                    t_val = float(m_t.group(1))
                    matching_wids = [w.id for w in transcript.words if abs(w.start - t_val) <= 0.6]
                    if matching_wids:
                        candidate_hits.append(matching_wids)

        if not candidate_hits:
            return "rejected", None, {"reason": "No candidate hits in pending clarification."}
        occ = target.occurrence
        chosen_wids: list[int] = []
        if occ == "all":
            chosen_wids = [wid for h in candidate_hits for wid in h]
        elif occ in (1, "1", "first"):
            chosen_wids = candidate_hits[0]
        elif occ in (2, "2", "second"):
            chosen_wids = candidate_hits[1] if len(candidate_hits) > 1 else candidate_hits[0]
        elif occ in (3, "3", "third"):
            chosen_wids = candidate_hits[2] if len(candidate_hits) > 2 else candidate_hits[-1]
        else:
            chosen_wids = candidate_hits[0]

        target_act = pending_clarification.get("action", "cut")
        if target_act == "cut":
            return "applied", [CutRange(word_ids=chosen_wids)], {"summary": f"Removed selected occurrence ({target.occurrence})."}
        elif target_act == "speed":
            factor = pending_clarification.get("factor", 2.0)
            return "applied", [SpeedRange(word_ids=chosen_wids, factor=factor)], {"summary": f"Sped up selected occurrence ({target.occurrence}) by {factor}x."}
        elif target_act == "caption":
            text_val = pending_clarification.get("text", "Caption")
            return "applied", [AddCaption(word_ids=chosen_wids, text=text_val)], {"summary": f"Added caption to selected occurrence ({target.occurrence})."}
        else:
            return "applied", [CutRange(word_ids=chosen_wids)], {"summary": f"Applied operation to selected occurrence ({target.occurrence})."}

    if action == "caption":
        # Speaker-targeted captioning (e.g. "add captions for Dave", "caption speaker 1")
        target_spk = (target.speaker or "").strip().lower()
        if not target_spk and target.query and (
            target.query.lower().startswith("speaker")
            or any(target.query.lower() == (w.speaker or "").lower() for w in transcript.words if (w.speaker or "").lower() != "s1")
        ):
            target_spk = target.query.strip().lower()

        if target_spk:
            matching_words = [
                w for w in transcript.words
                if target_spk in (w.speaker or "").lower()
                or (target_spk in ("speaker 1", "s1", "1") and (w.speaker or "").lower() in ("speaker 1", "s1", "1"))
                or (target_spk in ("speaker 2", "s2", "2") and (w.speaker or "").lower() in ("speaker 2", "s2", "2"))
            ]
            if matching_words:
                chunks: list[list[Word]] = []
                cur_chunk: list[Word] = []
                for w in matching_words:
                    if not cur_chunk:
                        cur_chunk.append(w)
                    elif (
                        w.sentence_end
                        or (w.start - cur_chunk[-1].end >= 0.4)
                        or len(cur_chunk) >= 5
                        or len(" ".join(item.text for item in cur_chunk + [w])) > 32
                    ):
                        chunks.append(cur_chunk)
                        cur_chunk = [w]
                    else:
                        cur_chunk.append(w)
                if cur_chunk:
                    chunks.append(cur_chunk)

                ops: list[Operation] = []
                spk_name = matching_words[0].speaker or target_spk
                spk_color = target.color or ("#FACC15" if "1" in target_spk or "s1" in target_spk else "#38BDF8")
                for ch in chunks:
                    wids = [w.id for w in ch]
                    text_str = " ".join(w.text for w in ch).upper()
                    ops.append(AddCaption(word_ids=wids, text=text_str, color=spk_color))
                return "applied", ops, {"summary": f"Added captions for {spk_name} ({len(ops)} timed subtitle lines generated)."}

        if target.query == "__all__":
            chunks = chunk_transcript_for_captions(transcript)
            ops: list[Operation] = []
            for ch in chunks:
                wids = [w.id for w in ch]
                text_str = " ".join(w.text for w in ch).upper()
                ops.append(AddCaption(word_ids=wids, text=text_str))
            return "applied", ops, {"summary": f"Added captions to the whole video ({len(ops)} timed subtitle lines generated)."}

        cap_text = (target.text or target.query or "Caption").upper()
        query_phrase = target.query or target.text
        if not query_phrase:
            wids = [w.id for w in transcript.words[:min(3, len(transcript.words))]]
            return "applied", [AddCaption(word_ids=wids, text=cap_text)], {"summary": f"Added caption '{cap_text}'."}

        hits = phrase_search(transcript, query_phrase)
        if not hits:
            hits = fuzzy_phrase_search(transcript, query_phrase)
        if not hits:
            return "rejected", None, {"reason": f"Phrase '{query_phrase}' not found in transcript."}

        word_times = get_timeline_word_times(transcript, timeline)
        active_hits = [h for h in hits if any(wid in word_times for wid in h)]
        if not active_hits:
            return "rejected", None, {"reason": f"Phrase '{query_phrase}' is not on current timeline."}

        if target.time_hint is not None:
            hit_candidates: list[tuple[float, float, list[int]]] = []
            for h in active_hits:
                ws = word_times[h[0]][0]
                d = abs(ws - target.time_hint)
                if d <= TIME_HINT_TOLERANCE_SECS:
                    hit_candidates.append((d, ws, h))
            if hit_candidates:
                hit_candidates.sort(key=lambda c: (round(c[0], 6), c[1]))
                best_hit = hit_candidates[0][2]
                return "applied", [AddCaption(word_ids=best_hit, text=cap_text)], {"summary": f"Added caption '{cap_text}'."}

        if len(active_hits) > 1:
            options = [f"Occurrence {i+1} (at ~{word_times[h[0]][0]:.1f}s)" for i, h in enumerate(active_hits)]
            return "needs_clarification", None, {
                "question": f"'{query_phrase}' occurs {len(active_hits)} times. Which one?",
                "options": options,
                "phrase": query_phrase,
                "candidate_hits": active_hits,
                "action": "caption",
                "text": cap_text,
            }

        return "applied", [AddCaption(word_ids=active_hits[0], text=cap_text)], {"summary": f"Added caption '{cap_text}'."}

    if action == "cut":
        # Speaker-targeted cutting (e.g. "cut speaker 2")
        target_spk = (target.speaker or "").strip().lower()
        if not target_spk and target.query and target.query.lower().startswith("speaker"):
            target_spk = target.query.strip().lower()

        if target_spk:
            matching_wids = [
                w.id for w in transcript.words
                if target_spk in (w.speaker or "").lower()
                or (target_spk in ("speaker 1", "s1", "1") and (w.speaker or "").lower() in ("speaker 1", "s1", "1"))
                or (target_spk in ("speaker 2", "s2", "2") and (w.speaker or "").lower() in ("speaker 2", "s2", "2"))
            ]
            if matching_wids:
                return "applied", [CutRange(word_ids=matching_wids)], {"summary": f"Cut all segments spoken by {target.speaker or target.query}."}

        if target.start_time is not None and target.end_time is not None:
            if target.start_time >= timeline.total_duration:
                return "rejected", None, {"reason": f"Start time {target.start_time:.1f}s exceeds video duration ({timeline.total_duration:.1f}s)."}
            wids = time_range_to_word_ids(transcript, timeline, target.start_time, target.end_time)
            if not wids:
                return "rejected", None, {"reason": f"No words found between {target.start_time}s and {target.end_time}s."}
            return "applied", [CutRange(word_ids=wids)], {"summary": f"Cut from {target.start_time:.1f}s to {target.end_time:.1f}s."}

        if target.query:
            phrase = target.query.strip()
            hits = phrase_search(transcript, phrase)
            if not hits:
                hits = fuzzy_phrase_search(transcript, phrase)
            if not hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}

            word_times = get_timeline_word_times(transcript, timeline)
            active_hits = [h for h in hits if any(wid in word_times for wid in h)]
            if not active_hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' was already cut from current timeline."}

            if target.time_hint is not None:
                hit_candidates = []
                for h in active_hits:
                    ws = word_times[h[0]][0]
                    d = abs(ws - target.time_hint)
                    if d <= TIME_HINT_TOLERANCE_SECS:
                        hit_candidates.append((d, ws, h))
                if hit_candidates:
                    # Deterministic tie-breaker:
                    # 1. distance (rounded to 6 decimal places)
                    # 2. timeline start time ascending (earlier in timeline wins!)
                    hit_candidates.sort(key=lambda c: (round(c[0], 6), c[1]))
                    best_hit = hit_candidates[0][2]
                    return "applied", [CutRange(word_ids=best_hit)], {"summary": f"Removed '{phrase}' near {target.time_hint:.2f}s."}
                else:
                    options = [f"Occurrence {i+1} (at ~{word_times[h[0]][0]:.1f}s)" for i, h in enumerate(active_hits)]
                    options.append("All occurrences")
                    return "needs_clarification", None, {
                        "question": f"'{phrase}' has {len(active_hits)} occurrences, none within {TIME_HINT_TOLERANCE_SECS}s of {target.time_hint}s. Which one?",
                        "options": options,
                        "phrase": phrase,
                        "candidate_hits": active_hits,
                        "action": "cut",
                    }

            if len(active_hits) == 1:
                return "applied", [CutRange(word_ids=active_hits[0])], {"summary": f"Removed '{phrase}'."}
            else:
                options = [f"Occurrence {i+1} (at ~{word_times[h[0]][0]:.1f}s)" for i, h in enumerate(active_hits)]
                options.append("All occurrences")
                return "needs_clarification", None, {
                    "question": f"'{phrase.capitalize()}' is mentioned {len(active_hits)} times. Which one, or all?",
                    "options": options,
                    "phrase": phrase,
                    "candidate_hits": active_hits,
                    "action": "cut",
                }

    if action == "speed":
        factor = target.factor or 2.0
        if target.start_time is not None and target.end_time is not None:
            if target.start_time >= timeline.total_duration:
                return "rejected", None, {"reason": f"Start time {target.start_time:.1f}s exceeds video duration ({timeline.total_duration:.1f}s)."}
            wids = time_range_to_word_ids(transcript, timeline, target.start_time, target.end_time)
            if not wids:
                return "rejected", None, {"reason": f"No words found between {target.start_time}s and {target.end_time}s."}
            return "applied", [SpeedRange(word_ids=wids, factor=factor)], {"summary": f"Sped up from {target.start_time}s to {target.end_time}s by {factor}x."}

        if target.query:
            phrase = target.query.strip()
            hits = phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)
            if not hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}
            word_times = get_timeline_word_times(transcript, timeline)
            active_hits = [h for h in hits if any(wid in word_times for wid in h)]
            if not active_hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' is not on current timeline."}
            if len(active_hits) > 1 and target.time_hint is None:
                options = [f"Occurrence {i+1} (at ~{word_times[h[0]][0]:.1f}s)" for i, h in enumerate(active_hits)]
                return "needs_clarification", None, {
                    "question": f"'{phrase}' occurs {len(active_hits)} times. Which one?",
                    "options": options,
                    "phrase": phrase,
                    "candidate_hits": active_hits,
                    "action": "speed",
                    "factor": factor,
                }
            return "applied", [SpeedRange(word_ids=active_hits[0], factor=factor)], {"summary": f"Sped up '{phrase}' by {factor}x."}

    if action in ("zoom", "zoom_speaker", "punch_in"):
        factor = target.factor or 1.2
        if target.start_time is not None and target.end_time is not None:
            if target.start_time >= timeline.total_duration:
                return "rejected", None, {"reason": f"Start time {target.start_time:.1f}s exceeds video duration ({timeline.total_duration:.1f}s)."}
            wids = time_range_to_word_ids(transcript, timeline, target.start_time, target.end_time)
            if not wids:
                return "rejected", None, {"reason": f"No words found between {target.start_time}s and {target.end_time}s."}
            return "applied", [ZoomSpeaker(word_ids=wids, factor=factor)], {"summary": f"Zoomed in {factor}x from {target.start_time:.1f}s to {target.end_time:.1f}s."}

        if target.query:
            phrase = target.query.strip()
            if phrase in ("speaker", "the speaker", "video", "me", "talking head", "__all__"):
                surviving_wids = [wid for seg in timeline.segments for wid in seg.source_word_ids]
                if not surviving_wids:
                    surviving_wids = [w.id for w in transcript.words]
                return "applied", [ZoomSpeaker(word_ids=surviving_wids, factor=factor)], {"summary": f"Zoomed in on the speaker by {factor}x."}

            hits = phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)
            if not hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}
            word_times = get_timeline_word_times(transcript, timeline)
            active_hits = [h for h in hits if any(wid in word_times for wid in h)]
            if not active_hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' is not on current timeline."}
            if len(active_hits) > 1 and target.time_hint is None:
                options = [f"Occurrence {i+1} (at ~{word_times[h[0]][0]:.1f}s)" for i, h in enumerate(active_hits)]
                return "needs_clarification", None, {
                    "question": f"'{phrase}' occurs {len(active_hits)} times. Which one?",
                    "options": options,
                    "phrase": phrase,
                    "candidate_hits": active_hits,
                    "action": "zoom",
                    "factor": factor,
                }
            return "applied", [ZoomSpeaker(word_ids=active_hits[0], factor=factor)], {"summary": f"Zoomed in {factor}x on '{phrase}'."}

        surviving_wids = [wid for seg in timeline.segments for wid in seg.source_word_ids]
        if not surviving_wids:
            surviving_wids = [w.id for w in transcript.words]
        return "applied", [ZoomSpeaker(word_ids=surviving_wids, factor=factor)], {"summary": f"Zoomed in on the speaker by {factor}x."}

    if action in ("bleep", "bleep_only"):
        phrase = (target.query or "").strip()
        hits = (phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)) if phrase else []
        if not hits and phrase:
            return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}

        target_spk = (target.speaker or "").strip().lower()
        if target_spk and hits:
            spk_word_ids = {
                w.id
                for w in transcript.words
                if target_spk in (w.speaker or "").lower()
                or (target_spk in ("speaker 1", "s1", "1") and (w.speaker or "").lower() in ("speaker 1", "s1", "1"))
                or (target_spk in ("speaker 2", "s2", "2") and (w.speaker or "").lower() in ("speaker 2", "s2", "2"))
            }
            hits = [h for h in hits if any(wid in spk_word_ids for wid in h)]
            if not hits:
                return "rejected", None, {"reason": f"Phrase '{phrase}' spoken by {target.speaker} not found in transcript."}

        if not hits:
            if target.start_time is not None and target.end_time is not None:
                wids = time_range_to_word_ids(transcript, timeline, target.start_time, target.end_time)
                if not wids:
                    return "rejected", None, {"reason": f"No words found between {target.start_time}s and {target.end_time}s."}
                return "applied", [BleepWords(word_ids=wids)], {"summary": f"Bleeped audio from {target.start_time}s to {target.end_time}s."}
            return "rejected", None, {"reason": "No target word or time range provided to bleep."}

        word_times = get_timeline_word_times(transcript, timeline)
        active_hits = [h for h in hits if any(wid in word_times for wid in h)]
        if not active_hits:
            active_hits = hits

        occ = target.occurrence
        if action == "bleep" and (occ in (None, "all", "all occurrences")):
            all_wids = [wid for h in active_hits for wid in h]
            spk_suffix = f" spoken by {target.speaker.capitalize()}" if target.speaker else ""
            return "applied", [BleepWords(word_ids=all_wids)], {"summary": f"Bleeped all occurrences of '{phrase}'{spk_suffix} ({len(active_hits)} occurrences)."}

        occ_idx = 0
        if isinstance(occ, int):
            occ_idx = max(0, min(occ - 1, len(active_hits) - 1))
        elif isinstance(occ, str):
            if occ in ("first", "1", "1st"):
                occ_idx = 0
            elif occ in ("second", "2", "2nd"):
                occ_idx = min(1, len(active_hits) - 1)
            elif occ in ("third", "3", "3rd"):
                occ_idx = min(2, len(active_hits) - 1)

        targeted_hit = active_hits[occ_idx]
        other_hits = [h for i, h in enumerate(active_hits) if i != occ_idx]
        other_wids = [wid for h in other_hits for wid in h]

        ops: list[Operation] = [BleepWords(word_ids=targeted_hit)]
        if other_wids:
            ops.append(UnbleepWords(word_ids=other_wids))
            summary = f"Bleeped {occ or 'first'} occurrence of '{phrase}' and removed bleep from others."
        else:
            summary = f"Bleeped {occ or 'first'} occurrence of '{phrase}'."

        return "applied", ops, {"summary": summary}

    if action == "unbleep":
        phrase = (target.query or "").strip()
        hits = (phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)) if phrase else []

        if not phrase:
            bleeped_wids: set[int] = set()
            for seg in timeline.segments:
                if seg.is_bleeped:
                    bleeped_wids.update(seg.source_word_ids)
            if not bleeped_wids:
                return "rejected", None, {"reason": "No audio currently bleeped to remove."}
            occ = target.occurrence
            if occ in ("other", "others", "rest"):
                sorted_wids = sorted(list(bleeped_wids))
                if len(sorted_wids) > 1:
                    unbleep_wids = sorted_wids[1:]
                    return "applied", [UnbleepWords(word_ids=unbleep_wids)], {"summary": f"Removed bleep from other words ({len(unbleep_wids)} words)."}
            return "applied", [UnbleepWords(word_ids=sorted(list(bleeped_wids)))], {"summary": "Removed bleep from all words."}

        if not hits:
            return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}

        word_times = get_timeline_word_times(transcript, timeline)
        active_hits = [h for h in hits if any(wid in word_times for wid in h)]
        if not active_hits:
            active_hits = hits

        occ = target.occurrence
        if occ in (None, "all", "all occurrences"):
            all_wids = [wid for h in active_hits for wid in h]
            return "applied", [UnbleepWords(word_ids=all_wids)], {"summary": f"Removed bleep from all occurrences of '{phrase}'."}

        if occ in ("other", "others", "rest"):
            other_hits = active_hits[1:] if len(active_hits) > 1 else []
            other_wids = [wid for h in other_hits for wid in h]
            if not other_wids:
                return "rejected", None, {"reason": f"No other occurrences of '{phrase}' found to unbleep."}
            return "applied", [UnbleepWords(word_ids=other_wids)], {"summary": f"Removed bleep from other occurrences of '{phrase}'."}

        occ_idx = 0
        if isinstance(occ, int):
            occ_idx = max(0, min(occ - 1, len(active_hits) - 1))
        elif isinstance(occ, str):
            if occ in ("first", "1", "1st"):
                occ_idx = 0
            elif occ in ("second", "2", "2nd"):
                occ_idx = min(1, len(active_hits) - 1)
            elif occ in ("third", "3", "3rd"):
                occ_idx = min(2, len(active_hits) - 1)

        targeted_hit = active_hits[occ_idx]
        return "applied", [UnbleepWords(word_ids=targeted_hit)], {"summary": f"Removed bleep from {occ or 'first'} occurrence of '{phrase}'."}

    if action in ("unmask_captions", "unmask"):
        phrase = (target.query or "").strip()
        hits = (phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)) if phrase else []
        if phrase and not hits:
            return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}

        if hits:
            wids = [wid for h in hits for wid in h]
            return "applied", [UnmaskCaptions(word_ids=wids)], {"summary": f"Unmasked '{phrase}' in captions."}
        else:
            return "applied", [UnmaskCaptions(word_ids=[])], {"summary": "Unmasked bleeped words in captions."}

    if action in ("mask_captions", "mask", "remask_captions", "remask"):
        phrase = (target.query or "").strip()
        hits = (phrase_search(transcript, phrase) or fuzzy_phrase_search(transcript, phrase)) if phrase else []
        if phrase and not hits:
            return "rejected", None, {"reason": f"Phrase '{phrase}' not found in transcript."}

        if hits:
            wids = [wid for h in hits for wid in h]
            return "applied", [MaskCaptions(word_ids=wids)], {"summary": f"Masked '{phrase}' in captions."}
        else:
            return "applied", [MaskCaptions(word_ids=[])], {"summary": "Masked bleeped words in captions with asterisks."}

    return "rejected", None, {"reason": f"Unrecognized intent target action: '{target.action}'"}


def resolve_interpretation_result(
    result: InterpretationResult,
    transcript: Transcript,
    timeline: Timeline,
    log: OperationLog,
    pending_clarification: dict[str, Any] | None = None,
) -> tuple[str, list[Operation] | None, dict[str, Any]]:
    """
    Resolve a full InterpretationResult into engine operations.
    """
    if result.status == "rejected":
        return "rejected", None, {"reason": result.rejection_reason or "Instruction rejected."}

    if result.status == "needs_clarification":
        return "needs_clarification", None, {
            "question": result.clarification_question or "Please clarify your request.",
            "options": result.clarification_options or [],
        }

    if not result.targets:
        if result.status in ("undo", "redo", "render", "restore"):
            return resolve_intent_target(
                IntentTarget(action=result.status),
                transcript,
                timeline,
                log,
                pending_clarification,
            )
        return "rejected", None, {"reason": result.rejection_reason or "No target operations extracted."}

    all_ops: list[Operation] = []
    summaries: list[str] = []

    for target in result.targets:
        st, ops, det = resolve_intent_target(
            target,
            transcript,
            timeline,
            log,
            pending_clarification,
        )
        if st in ("needs_clarification", "rejected", "undo", "redo", "render", "restore"):
            return st, ops, det
        if ops:
            all_ops.extend(ops)
        if det.get("summary"):
            summaries.append(det["summary"])

    return "applied", all_ops, {"summary": " ".join(summaries) or "Operations applied."}
