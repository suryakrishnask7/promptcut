"""
Rule-Based Instruction Parser for Session Runner.

Parses natural language edit prompts into structured promptcut operations
(cut, keep, speed, caption, undo, redo, restore, render) without external LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption,
    CutRange,
    KeepRange,
    Operation,
    RemoveFillers,
    RemoveSilences,
    SetCaptionStyle,
    SpeedRange,
    ZoomSpeaker,
)
from promptcut.models.timeline import Timeline
from promptcut.models.transcript import Transcript, Word
from promptcut.resolver import (
    fuzzy_phrase_search,
    parse_time_expression,
    phrase_search,
    time_range_to_word_ids,
)


from promptcut.render.caption_chunker import chunk_transcript_for_captions


def parse_instruction(
    say: str,
    transcript: Transcript,
    log: OperationLog,
    timeline: Timeline,
    answer: str | None = None,
) -> tuple[str, list[Operation] | None, dict[str, Any]]:
    """
    Parse a user utterance into engine operations or clarification request.

    Parameters
    ----------
    say : str
        User prompt string.
    transcript : Transcript
        Source transcript.
    log : OperationLog
        Current operation log state.
    timeline : Timeline
        Current output timeline.
    answer : str | None
        Follow-up response if the step expects a clarification.

    Returns
    -------
    tuple[status, ops_to_push, details]
        status: 'applied' | 'needs_clarification' | 'rejected' | 'render'
        ops_to_push: list of operations to push onto log, or None
        details: dict with 'question', 'options', 'reason', etc.
    """
    text = say.strip()
    lower = text.lower()

    # 0. Check Natural Language with time hint & tolerance
    from promptcut.interpreter.base import InterpreterContext
    from promptcut.interpreter.mock import MockInterpreter
    from promptcut.resolver import resolve_intent_target

    interp_res = MockInterpreter().interpret(
        text,
        InterpreterContext(duration=timeline.total_duration, time_policy="current"),
    )
    if interp_res.status == "applied" and interp_res.targets:
        target = interp_res.targets[0]
        if target.time_hint is not None:
            st, ops_t, det_t = resolve_intent_target(target, transcript, timeline, log)
            if st != "rejected":
                return st, ops_t, det_t

    # 1. RENDER
    if lower == "render":
        return "render", [], {"summary": "Render request received."}

    # 2. UNDO
    if lower == "undo":
        return "undo", [], {"summary": "Reversed last instruction."}

    # 3. REDO
    if lower == "redo":
        return "redo", [], {"summary": "Re-applied last undone instruction."}

    # SPEAKER-CONDITIONED BLEEP:
    # "bleep every time vanessa says and", "bleep whenever dan says like", "bleep speaker 1 when they say and"
    # "bleep the word and every time vanessa says it", "bleep 'and' when vanessa says it"
    m_spk_bleep1 = re.search(
        r"\bbleep\s+(?:every\s+time|whenever|when)\s+([a-zA-Z0-9_\-\s]+?)\s+says?\s+(?:the\s+word\s+)?[\"']?([a-zA-Z0-9_\-]+)[\"']?",
        lower,
    )
    m_spk_bleep2 = re.search(
        r"\bbleep\s+(?:the\s+word\s+)?[\"']?([a-zA-Z0-9_\-]+)[\"']?\s+(?:every\s+time|whenever|when)\s+([a-zA-Z0-9_\-\s]+?)\s+says?",
        lower,
    )
    if m_spk_bleep1 or m_spk_bleep2:
        if m_spk_bleep1:
            target_spk = m_spk_bleep1.group(1).strip()
            target_phrase = m_spk_bleep1.group(2).strip()
        else:
            target_phrase = m_spk_bleep2.group(1).strip()
            target_spk = m_spk_bleep2.group(2).strip()

        hits = phrase_search(transcript, target_phrase)
        if not hits:
            hits = fuzzy_phrase_search(transcript, target_phrase)
        if not hits:
            return "rejected", None, {"reason": f"Phrase '{target_phrase}' not found in transcript."}

        spk_lower = target_spk.lower()
        spk_word_ids = {
            w.id
            for w in transcript.words
            if spk_lower in (w.speaker or "").lower()
            or (spk_lower in ("speaker 1", "s1", "1") and (w.speaker or "").lower() in ("speaker 1", "s1", "1"))
            or (spk_lower in ("speaker 2", "s2", "2") and (w.speaker or "").lower() in ("speaker 2", "s2", "2"))
        }
        matching_hits = [h for h in hits if any(wid in spk_word_ids for wid in h)]
        if not matching_hits:
            return "rejected", None, {"reason": f"Phrase '{target_phrase}' spoken by {target_spk} was not found in transcript."}

        all_wids: list[int] = []
        for h in matching_hits:
            all_wids.extend(h)
        from promptcut.models.operations import BleepWords, MaskCaptions

        ops = [BleepWords(word_ids=all_wids), MaskCaptions(word_ids=all_wids)]
        return "applied", ops, {"summary": f"Bleeped all occurrences of '{target_phrase}' spoken by {target_spk.capitalize()} ({len(matching_hits)} occurrences)."}

    # BLEEP: "bleep 'wonder'", "bleep the word welcome", "bleep <word>"
    # Guard against words like "every", "whenever", "each", "all", "speaker" if followed by conversational text
    m_bleep = re.search(r"\bbleep\s+(?:the\s+word\s+)?[\"']?([a-zA-Z0-9_\-]+)[\"']?", lower)
    if m_bleep and m_bleep.group(1).strip().lower() not in ("every", "whenever", "when", "each", "all", "speaker", "to"):
        target_phrase = m_bleep.group(1).strip()
        hits = phrase_search(transcript, target_phrase)
        if not hits:
            return "rejected", None, {"reason": f"Phrase '{target_phrase}' not found in transcript."}
        from promptcut.models.operations import BleepWords, MaskCaptions
        all_wids: list[int] = []
        for h in hits:
            all_wids.extend(h)
        ops = [BleepWords(word_ids=all_wids), MaskCaptions(word_ids=all_wids)]
        return "applied", ops, {"summary": f"Bleeped all occurrences of '{target_phrase}'."}

    # UNMASK CAPTIONS: "unmask captions", "unmask caption"
    if "unmask caption" in lower or "unmask captions" in lower:
        from promptcut.models.operations import UnmaskCaptions
        all_wids = [w.id for w in transcript.words]
        return "applied", [UnmaskCaptions(word_ids=all_wids)], {"summary": "Unmasked captions."}

    # REMASK CAPTIONS: "remask captions", "mask captions"
    if "remask caption" in lower or "remask captions" in lower or lower == "mask captions":
        from promptcut.models.operations import MaskCaptions
        bleeped_wids = []
        for op in log.active_operations:
            if hasattr(op, "word_ids") and type(op).__name__ == "BleepWords":
                bleeped_wids.extend(op.word_ids)
        return "applied", [MaskCaptions(word_ids=bleeped_wids)], {"summary": "Remasked captions for bleeped words."}

    # 4. CAPTION THE WHOLE VIDEO / WHOLE TRANSCRIPT / ALL
    # - "add caption to the whole video"
    # - "add captions to the whole video"
    # - "caption the whole video"
    # - "caption the whole transcript"
    # - "caption whole video"
    # - "auto caption" / "auto captions" / "autocaption"
    # - "subtitle whole video"
    is_caption_all = (
        bool(re.search(r"(?:add\s+)?captions?\s+(?:to\s+|for\s+)?(?:the\s+)?(whole|entire|all)\s+(video|transcript)", lower))
        or bool(re.search(r"caption\s+(?:the\s+)?(whole|entire|all)\s+(video|transcript)", lower))
        or bool(re.search(r"(?:auto\s*caption|autocaption|subtitle\s+(?:the\s+)?(?:whole|entire)\s+video)", lower))
        or lower in ("caption all", "caption everything", "auto caption", "auto captions", "autocaption", "caption whole video", "caption whole transcript")
    )
    if is_caption_all:
        if not transcript.words:
            return "rejected", None, {"reason": "Transcript has no words to caption."}

        chunks = chunk_transcript_for_captions(transcript)
        ops: list[Operation] = []
        for ch in chunks:
            text_str = " ".join(w.text for w in ch)
            wids = [w.id for w in ch]
            ops.append(AddCaption(word_ids=wids, text=text_str))

        return "applied", ops, {
            "summary": f"Added captions to the whole video ({len(ops)} timed subtitle lines generated)."
        }

    # 5. CAPTION POSITION ("move the caption to the middle", "center the caption", "move caption to top")
    m_pos = re.search(
        r"(?:(?:move|position|align|place|put)\s+(?:the\s+|that\s+)?caption\s+(?:to\s+|at\s+|in\s+)?(?:the\s+)?(middle|center|top|bottom))|(?:center\s+(?:the\s+|that\s+)?caption)",
        lower,
    )
    if m_pos:
        pos_raw = (m_pos.group(1) or "middle").strip()
        pos = "middle" if pos_raw in ("middle", "center") else pos_raw
        op = SetCaptionStyle(position=pos)
        return "applied", [op], {"summary": f"Moved caption to {pos}."}

    # 5. CAPTION FONT STYLE ("make caption italic", "make captions bold", "font style normal")
    m_fstyle = re.search(
        r"(?:make\s+(?:the\s+|that\s+)?captions?\s+(italic|bold|normal))|(?:(?:set|change)?\s*(?:caption\s+)?font\s+style\s+(?:to\s+)?(italic|bold|normal))|(?:(italicize|bold)\s+(?:the\s+|that\s+)?captions?)",
        lower,
    )
    if m_fstyle:
        raw_s = m_fstyle.group(1) or m_fstyle.group(2) or m_fstyle.group(3) or "normal"
        style = "italic" if "italic" in raw_s else ("bold" if "bold" in raw_s else "normal")
        op = SetCaptionStyle(font_style=style)
        return "applied", [op], {"summary": f"Set caption font style to {style}."}

    # 6. CAPTION FONT SIZE ("increase font size", "decrease caption font size", "font size 40")
    m_size_num = re.search(
        r"(?:(?:set|change)\s+)?(?:caption\s+)?font\s+size\s+(?:to\s+)?(\d+)|font\s+size\s+(\d+)",
        lower,
    )
    if m_size_num:
        new_size = int(m_size_num.group(1) or m_size_num.group(2))
        op = SetCaptionStyle(font_size=new_size)
        return "applied", [op], {"summary": f"Set caption font size to {new_size}."}

    if any(k in lower for k in [
        "increase font size", "increase caption font size", "increase caption size",
        "make caption bigger", "make captions bigger", "bigger caption", "bigger captions",
        "larger font", "larger caption",
    ]):
        cur_size = 32
        if timeline and timeline.captions:
            cur_size = getattr(timeline.captions[0], "font_size", 32) or 32
        new_size = cur_size + 8
        op = SetCaptionStyle(font_size=new_size)
        return "applied", [op], {"summary": f"Increased caption font size to {new_size}."}

    if any(k in lower for k in [
        "decrease font size", "decrease caption font size", "decrease caption size",
        "make caption smaller", "make captions smaller", "smaller caption", "smaller captions",
    ]):
        cur_size = 32
        if timeline and timeline.captions:
            cur_size = getattr(timeline.captions[0], "font_size", 32) or 32
        new_size = max(16, cur_size - 8)
        op = SetCaptionStyle(font_size=new_size)
        return "applied", [op], {"summary": f"Decreased caption font size to {new_size}."}

    # 7. CHANGE / SET CAPTION TEXT ("change caption to 'Hello'", "set caption text to 'Welcome'")
    m_cap_text = re.search(
        r"(?:change|set|edit|update)\s+(?:the\s+|that\s+)?caption(?:\s+text)?\s+to\s+['\"]?(.+?)['\"]?$",
        text,
        re.IGNORECASE,
    )
    if not m_cap_text:
        m_cap_text = re.search(
            r"make\s+(?:the\s+|that\s+)?caption\s+say\s+['\"]?(.+?)['\"]?$",
            text,
            re.IGNORECASE,
        )
    if m_cap_text:
        new_text = m_cap_text.group(1).strip(" '\"")
        op = SetCaptionStyle(text=new_text)
        return "applied", [op], {"summary": f"Updated caption text to '{new_text}'."}

    # 8. CAPTION COLOR ("make that caption red", "make caption yellow")
    m_color = re.search(
        r"(?:make\s+(?:that\s+|the\s+)?caption\s+|(?:set|change)\s+(?:caption\s+)?color\s+(?:to\s+)?)(red|green|blue|yellow|white|black|cyan|magenta|orange|pink|purple|gold)",
        lower,
    )
    if m_color:
        color = m_color.group(1)
        op = SetCaptionStyle(color=color)
        return "applied", [op], {"summary": f"Set caption color to {color}."}

    # 9. CORRECT TRANSCRIPT WORD PRONUNCIATION / SPELLING
    # - "correct word 'foo' to 'bar'"
    # - "replace word 'foo' with 'bar'"
    # - "change word 'foo' to 'bar'"
    m_wcorr = re.search(
        r"(?:correct|replace|change)\s+word\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?\s+(?:to|with)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?",
        lower,
    )
    if m_wcorr:
        old_w = m_wcorr.group(1).strip()
        new_w = m_wcorr.group(2).strip()
        matched_w = None
        for w in transcript.words:
            if w.text.lower().strip(".,!?;:\"'—–-") == old_w.lower():
                matched_w = w
                break
        if matched_w is not None:
            return "correct_word", [], {
                "word_id": matched_w.id,
                "old_text": matched_w.text,
                "new_text": new_w,
                "summary": f"Corrected word #{matched_w.id} '{matched_w.text}' to '{new_w}'.",
            }
        else:
            return "rejected", None, {"reason": f"Word '{old_w}' not found in transcript."}

    # 10. ADD CAPTION
    # Supports:
    # - "add a caption 'Welcome!' when I say welcome to the channel"
    # - "add caption 'Hello' to/on/for/at welcome"
    # - "add caption 'Hello' at 2s"
    # - "caption 'Hello' when I say..."
    # - "add caption 'Hello World'"
    # - "add caption Hello World" (unquoted)
    m_cap = re.search(
        r"(?:add\s+)?(?:a\s+)?caption\s+['\"]([^'\"]+)['\"](?:\s+(?:when\s+i\s+say|to|on|for|at|from)\s+(.+))?",
        text,
        re.IGNORECASE,
    )
    if not m_cap and not any(k in lower for k in ["remove", "cut", "speed", "filler", "silence", "put", "restore"]):
        # Try unquoted caption: e.g. "add caption Welcome to the channel"
        m_cap = re.search(
            r"(?:add\s+)?(?:a\s+)?caption\s+([^'\"]+?)(?:\s+(?:when\s+i\s+say|to|on|for|at|from)\s+(.+))?$",
            text,
            re.IGNORECASE,
        )

    if m_cap:
        cap_text = m_cap.group(1).strip(" '\"")
        raw_target = (m_cap.group(2) or "").strip(" .!?,")

        wids: list[int] = []
        target_name = raw_target
        if not raw_target:
            if transcript.words:
                wids = [w.id for w in transcript.words[:min(3, len(transcript.words))]]
                target_name = "start of video"
            else:
                return "rejected", None, {"reason": "Transcript has no words to anchor caption to."}
        else:
            # Check if time expression
            try:
                t_val = parse_time_expression(raw_target)
                wids = time_range_to_word_ids(transcript, timeline, t_val, t_val + 2.0)
            except Exception:
                wids = []

            if not wids:
                phrase = raw_target
                hits = phrase_search(transcript, phrase)
                if not hits:
                    hits = fuzzy_phrase_search(transcript, phrase)
                if not hits:
                    # Check individual words in phrase
                    for single_w in phrase.split():
                        hits = phrase_search(transcript, single_w)
                        if hits:
                            break
                if not hits:
                    sample = " ".join(w.text for w in transcript.words[:10])
                    return "rejected", None, {
                        "reason": f"Phrase '{phrase}' not found in transcript. Detected speech starts with: \"{sample}...\""
                    }
                if len(hits) > 1 and not answer:
                    options = [f"Occurrence {i+1}" for i in range(len(hits))]
                    return "needs_clarification", None, {
                        "question": f"Phrase '{phrase}' mentioned {len(hits)} times. Which one?",
                        "options": options,
                    }
                elif len(hits) > 1 and answer:
                    ans_lower = answer.lower()
                    if "first" in ans_lower or "1" in ans_lower:
                        wids = hits[0]
                    elif "second" in ans_lower or "2" in ans_lower:
                        wids = hits[1] if len(hits) > 1 else hits[0]
                    elif "third" in ans_lower or "3" in ans_lower:
                        wids = hits[2] if len(hits) > 2 else hits[-1]
                    else:
                        wids = hits[0]
                else:
                    wids = hits[0]

        op = AddCaption(word_ids=wids, text=cap_text)
        return "applied", [op], {"summary": f"Added caption '{cap_text}' anchored to '{target_name}'."}


    # 6. RESTORE / PUT BACK ("actually, put the intro back", "put back the intro", "restore intro")
    if "put" in lower and "back" in lower or "restore" in lower:
        # Find the intro cut or most recent cut op in active history
        target_op_id: int | None = None
        for op in reversed(log.active_ops):
            if op.op_type == "cut_range":
                # Check if it was an intro cut (includes word 0)
                if 0 in getattr(op, "word_ids", []):
                    target_op_id = op.op_id
                    break
        if target_op_id is None:
            # Fallback to the last cut op
            for op in reversed(log.active_ops):
                if op.op_type == "cut_range":
                    target_op_id = op.op_id
                    break

        if target_op_id is not None:
            return "restore", [], {"target_op_id": target_op_id, "summary": "Restored earlier edit."}
        return "rejected", None, {"reason": "No matching cut found to restore."}

    # 7. CUT FIRST N SECONDS ("cut the first 30 seconds", "remove first 30s")
    m_first = re.search(r"(?:cut|remove)\s+(?:the\s+)?first\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?", lower)
    if m_first:
        secs = float(m_first.group(1))
        wids = time_range_to_word_ids(transcript, timeline, 0.0, secs)
        if not wids:
            return "rejected", None, {"reason": f"No words found in first {secs}s."}
        op = CutRange(word_ids=wids)
        return "applied", [op], {"summary": f"Cut the first {secs} seconds."}

    # 8. SPEED UP RANGE ("speed up from 1:00 to 1:30 by 2x", "speed up 0:30 to 1:00 by 1.5x")
    m_speed = re.search(r"speed\s+up\s+from\s+([0-9:]+)\s+to\s+([0-9:]+)\s+by\s+(\d+(?:\.\d+)?)x?", lower)
    if m_speed:
        t1_str, t2_str, factor_str = m_speed.group(1), m_speed.group(2), m_speed.group(3)
        t1 = parse_time_expression(t1_str)
        t2 = parse_time_expression(t2_str)
        factor = float(factor_str)
        wids = time_range_to_word_ids(transcript, timeline, t1, t2)
        if not wids:
            return "rejected", None, {"reason": f"No words found between {t1_str} and {t2_str}."}
        op = SpeedRange(word_ids=wids, factor=factor)
        return "applied", [op], {"summary": f"Sped up from {t1_str} to {t2_str} by {factor}x."}

    # 8b. ZOOM IN / PUNCH-IN ON SPEAKER OR PHRASE OR TIME RANGE
    # Handles: "zoom in on the speaker", "punch in by 1.2x", "zoom in on pricing", "zoom in from 0:10 to 0:20 by 1.2x"
    if "zoom" in lower or "punch in" in lower:
        # Check explicit time range first
        m_z_time = re.search(
            r"(?:zoom\s+in|punch\s+in|zoom)\s+(?:from\s+([0-9:]+)\s+to\s+([0-9:]+)|([0-9:]+)\s*[-–—]\s*([0-9:]+))(?:\s+by\s+(\d+(?:\.\d+)?)x?)?",
            lower,
        )
        if m_z_time:
            t1_str = m_z_time.group(1) or m_z_time.group(3)
            t2_str = m_z_time.group(2) or m_z_time.group(4)
            factor = float(m_z_time.group(5)) if m_z_time.group(5) else 1.2
            t1 = parse_time_expression(t1_str)
            t2 = parse_time_expression(t2_str)
            wids = time_range_to_word_ids(transcript, timeline, t1, t2)
            if not wids:
                return "rejected", None, {"reason": f"No words found between {t1_str} and {t2_str}."}
            op = ZoomSpeaker(word_ids=wids, factor=factor)
            return "applied", [op], {"summary": f"Zoomed in {factor}x from {t1_str} to {t2_str}."}

        # Check factor
        factor = 1.2
        m_f = re.search(r"by\s+(\d+(?:\.\d+)?)x?", lower)
        if m_f:
            factor = float(m_f.group(1))

        # Check target phrase or speaker
        m_target = re.search(r"(?:zoom\s+in|punch\s+in|zoom)(?:\s+(?:on\s+|when\s+i\s+say\s+|during\s+)?(.+?))?$", lower)
        target_str = m_target.group(1).strip() if m_target and m_target.group(1) else ""
        target_str = re.sub(r"\bby\s+\d+(?:\.\d+)?x?\b", "", target_str).strip()

        if not target_str or target_str in ("the speaker", "speaker", "video", "me", "talking head"):
            surviving_wids = [wid for seg in timeline.segments for wid in seg.source_word_ids]
            if not surviving_wids:
                surviving_wids = [w.id for w in transcript.words]
            op = ZoomSpeaker(word_ids=surviving_wids, factor=factor)
            return "applied", [op], {"summary": f"Zoomed in on the speaker by {factor}x."}
        else:
            hits = phrase_search(transcript, target_str)
            if not hits:
                hits = fuzzy_phrase_search(transcript, target_str)
            if hits:
                op = ZoomSpeaker(word_ids=hits[0], factor=factor)
                return "applied", [op], {"summary": f"Zoomed in {factor}x on '{target_str}'."}
            else:
                return "rejected", None, {"reason": f"Phrase '{target_str}' not found in transcript."}

    # 9. REMOVE FILLERS
    if "filler" in lower:
        op = RemoveFillers(filler_words=["um", "uh", "like"])
        return "applied", [op], {"summary": "Removed filler words (um, uh, like)."}

    # 10. REMOVE SILENCES
    if "silence" in lower:
        op = RemoveSilences()
        return "applied", [op], {"summary": "Removed silences."}

    # 10b. CUT WORD BY ID ("cut word #7", "remove word 7", "cut #7")
    m_cut_wid = re.search(r"(?:cut|remove)\s+(?:word\s+)?#?(\d+)$", lower)
    if m_cut_wid:
        target_wid = int(m_cut_wid.group(1))
        matched = [w for w in transcript.words if w.id == target_wid]
        if matched:
            op = CutRange(word_ids=[target_wid])
            return "applied", [op], {"summary": f"Cut word #{target_wid} ('{matched[0].text}')."}
        return "rejected", None, {"reason": f"Word #{target_wid} not found in transcript."}

    # 11. REMOVE PHRASE ("remove the part about pricing", "remove pricing", "cut pricing")
    m_phrase_cut = re.search(r"(?:remove|cut)\s+(?:the\s+part\s+about\s+)?([a-zA-Z0-9_\- ]+)", lower)
    if m_phrase_cut:
        target_phrase = m_phrase_cut.group(1).strip()
        hits = phrase_search(transcript, target_phrase)
        if not hits:
            hits = fuzzy_phrase_search(transcript, target_phrase)
        if hits:

            if len(hits) > 1 and not answer:
                options = [f"Occurrence {i+1} (at ~{transcript.word_by_id(hits[i][0]).start:.0f}s)" for i in range(len(hits))]
                options.append("All occurrences")
                return "needs_clarification", None, {
                    "question": f"'{target_phrase.capitalize()}' is mentioned {len(hits)} times. Which one, or all?",
                    "options": options,
                    "phrase": target_phrase,
                    "candidate_hits": hits,
                    "action": "cut",
                }
            elif len(hits) > 1 and answer:
                # Resolve answer
                ans_lower = answer.lower()
                chosen_wids: list[int] = []
                if "all" in ans_lower:
                    chosen_wids = [wid for hit in hits for wid in hit]
                elif "first" in ans_lower or "1" in ans_lower:
                    chosen_wids = hits[0]
                elif "second" in ans_lower or "2" in ans_lower:
                    chosen_wids = hits[1] if len(hits) > 1 else hits[0]
                elif "third" in ans_lower or "3" in ans_lower:
                    chosen_wids = hits[2] if len(hits) > 2 else hits[-1]
                else:
                    chosen_wids = hits[0]
                op = CutRange(word_ids=chosen_wids)
                return "applied", [op], {"summary": f"Removed '{target_phrase}' ({answer})."}
            else:
                op = CutRange(word_ids=hits[0])
                return "applied", [op], {"summary": f"Removed '{target_phrase}'."}
        else:
            return "rejected", None, {"reason": f"Phrase '{target_phrase}' not found in transcript."}

    return "rejected", None, {"reason": f"Unrecognized instruction: '{say}'"}

