"""
Speaker Diarization & Voice Identification for PromptCut.

Distinguishes talking characters in multi-person audio/video dialogues (e.g. 2-person podcasts,
interviews, conversations) and assigns continuous speaker labels (e.g. 'Speaker 1', 'Speaker 2',
or detected speaker names like 'Dave', 'Sarah').

Uses acoustic pitch estimation (fundamental frequency F0 via autocorrelation & spectral analysis)
powered by numpy & scipy, combined with Groq LLaMA conversational name detection.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

import numpy as np
import scipy.io.wavfile

from promptcut.config import DEFAULT_GROQ_MODEL
from promptcut.media.intake import find_executable
from promptcut.models.transcript import Transcript, Word

logger = logging.getLogger(__name__)


def estimate_voice_pitch(signal: np.ndarray, sample_rate: int = 16000) -> float:
    """
    Estimate fundamental frequency (F0) for a speech frame using normalized autocorrelation.
    Human voice F0 range: 75 Hz (deep male) to 340 Hz (high female).
    """
    if len(signal) < sample_rate * 0.06:  # Need at least 60ms
        return 0.0

    sig = signal.astype(np.float32)
    sig -= np.mean(sig)
    std = float(np.std(sig))
    if std < 1e-4:
        return 0.0
    sig /= std

    min_lag = int(sample_rate / 340)  # ~47 samples
    max_lag = int(sample_rate / 75)   # ~213 samples

    # Use central portion (up to 200ms)
    mid = len(sig) // 2
    win_len = min(len(sig), int(sample_rate * 0.2))
    frame = sig[max(0, mid - win_len // 2) : min(len(sig), mid + win_len // 2)]

    autocorr = np.correlate(frame, frame, mode='full')
    center = len(frame) - 1
    r = autocorr[center : center + max_lag + 1]

    if len(r) > max_lag:
        search_region = r[min_lag : max_lag + 1]
        if len(search_region) > 0 and np.max(search_region) > 0.20 * r[0]:
            peak_idx = min_lag + int(np.argmax(search_region))
            return float(sample_rate / peak_idx)

    return 0.0


def diarize_transcript_speakers(
    transcript: Transcript,
    media_path: str | Path | None = None,
    api_key: str | None = None,
) -> Transcript:
    """
    Analyze transcript words and assign speaker labels ('Speaker 1', 'Speaker 2', etc.).
    Preserves all word timings and IDs while updating Word.speaker.
    """
    words = transcript.words
    if not words or len(words) < 3:
        return transcript

    # 1. Primary: Groq LLM conversational dialogue diarization (high-accuracy dialogue & name recognition)
    key = api_key or os.environ.get("GROQ_API_KEY")
    p = Path(media_path).resolve() if media_path else None
    if key:
        try:
            return _diarize_with_groq_llm(transcript, key, media_path=p)
        except Exception as e:
            logger.warning(f"Groq LLM diarization error: {e}. Falling back to acoustic analysis.")

    # 2. Offline Fallback: Acoustic pitch diarization if media file is available
    if media_path:
        p = Path(media_path).resolve()
        if p.is_file():
            try:
                return _diarize_with_acoustic_pitch(transcript, p)
            except Exception as e:
                logger.warning(f"Acoustic pitch diarization error: {e}. Falling back to pause heuristics.")

    # 3. Last-resort Fallback: Inter-speech pause alternating heuristic
    return _diarize_with_acoustic_heuristics(transcript)


def _diarize_with_acoustic_pitch(transcript: Transcript, media_path: Path) -> Transcript:
    """
    Extract 16kHz mono audio and cluster speech turns by pitch (F0).
    Separates male voice (typically 85–150 Hz) and female voice (typically 175–250 Hz).
    """
    ffmpeg_bin = find_executable("ffmpeg")

    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "audio_16k.wav"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i", str(media_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-f", "wav",
            str(wav_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        sr, audio_data = scipy.io.wavfile.read(wav_path)

        words = transcript.words
        total_samples = len(audio_data)

        # Group words into speech turns (pause >= 0.4s or sentence end)
        turns: list[list[Word]] = []
        cur_turn: list[Word] = []

        for i, w in enumerate(words):
            if not cur_turn:
                cur_turn.append(w)
            else:
                prev_w = cur_turn[-1]
                gap = w.start - prev_w.end
                if gap >= 0.45 or (prev_w.sentence_end and gap >= 0.25):
                    turns.append(cur_turn)
                    cur_turn = [w]
                else:
                    cur_turn.append(w)
        if cur_turn:
            turns.append(cur_turn)

        # Estimate pitch for each turn
        turn_pitches: list[float] = []
        for t in turns:
            s_sec = max(0.0, t[0].start)
            e_sec = min(float(total_samples) / sr, t[-1].end)
            s_idx = int(s_sec * sr)
            e_idx = int(e_sec * sr)
            chunk = audio_data[s_idx:e_idx]

            pitch = estimate_voice_pitch(chunk, sr)
            turn_pitches.append(pitch)

        valid_pitches = [p for p in turn_pitches if 75.0 <= p <= 340.0]

        # Check if multiple speakers exist with distinct vocal pitch
        # If standard deviation is significant and range spans across 160Hz boundary:
        is_two_speakers = False
        pitch_threshold = 160.0

        if len(valid_pitches) >= 2:
            sorted_p = sorted(valid_pitches)
            low_half = sorted_p[: len(sorted_p) // 2]
            high_half = sorted_p[len(sorted_p) // 2 :]
            low_median = float(np.median(low_half))
            high_median = float(np.median(high_half))

            # In male-female talking heads, pitch difference is typically 50-100+ Hz
            if (high_median - low_median) >= 35.0:
                is_two_speakers = True
                pitch_threshold = (low_median + high_median) / 2.0
                logger.info(
                    f"Acoustic two-speaker detection confirmed: "
                    f"Lower Voice = {low_median:.1f}Hz, Higher Voice = {high_median:.1f}Hz, "
                    f"Separation Threshold = {pitch_threshold:.1f}Hz"
                )

        if not is_two_speakers:
            # Monologue or uniform speaker
            new_words = [
                Word(
                    id=w.id,
                    text=w.text,
                    start=w.start,
                    end=w.end,
                    speaker="Speaker 1",
                    confidence=w.confidence,
                    sentence_end=w.sentence_end,
                )
                for w in words
            ]
            return Transcript(words=new_words, title=transcript.title, source_duration=transcript.source_duration)

        # Assign Speaker 1 vs Speaker 2 based on acoustic pitch
        new_words = []
        current_assigned_speaker = "Speaker 1"

        for i, t in enumerate(turns):
            p = turn_pitches[i]
            if 75.0 <= p <= 340.0:
                speaker_label = "Speaker 1" if p < pitch_threshold else "Speaker 2"
                current_assigned_speaker = speaker_label
            else:
                speaker_label = current_assigned_speaker

            for w in t:
                new_words.append(
                    Word(
                        id=w.id,
                        text=w.text,
                        start=w.start,
                        end=w.end,
                        speaker=speaker_label,
                        confidence=w.confidence,
                        sentence_end=w.sentence_end,
                    )
                )

        return Transcript(words=new_words, title=transcript.title, source_duration=transcript.source_duration)


def _refine_speaker_names_with_llm(transcript: Transcript, api_key: str) -> Transcript:
    """
    If speakers introduce their names in the dialogue, replace 'Speaker 1'/'Speaker 2' with their real names.
    """
    try:
        from groq import Groq
        client = Groq(api_key=api_key, timeout=10.0)

        # Sample first 20 turns
        turns_summary = []
        cur_spk = None
        cur_text = []
        for w in transcript.words[:150]:
            if w.speaker != cur_spk:
                if cur_spk and cur_text:
                    turns_summary.append(f"{cur_spk}: {' '.join(cur_text)}")
                cur_spk = w.speaker
                cur_text = [w.text]
            else:
                cur_text.append(w.text)
        if cur_spk and cur_text:
            turns_summary.append(f"{cur_spk}: {' '.join(cur_text)}")

        prompt = (
            "Given these dialogue turns between Speaker 1 and Speaker 2, check if their actual names are stated.\n"
            "If a name is explicitly introduced or greeted (e.g. 'I am Mark', 'Thanks Sarah'), map:\n"
            "{\"Speaker 1\": \"Mark\", \"Speaker 2\": \"Sarah\"}\n"
            "If no personal names are stated, return {}\n"
            "Dialogue snippet:\n" + "\n".join(turns_summary)
        )

        res = client.chat.completions.create(
            model=DEFAULT_GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = json.loads(res.choices[0].message.content or "{}")
        spk1_name = data.get("Speaker 1") or data.get("speaker 1")
        spk2_name = data.get("Speaker 2") or data.get("speaker 2")

        if spk1_name or spk2_name:
            name_map = {}
            if spk1_name and len(spk1_name) <= 15:
                name_map["Speaker 1"] = spk1_name.capitalize()
            if spk2_name and len(spk2_name) <= 15:
                name_map["Speaker 2"] = spk2_name.capitalize()

            if name_map:
                new_words = [
                    Word(
                        id=w.id,
                        text=w.text,
                        start=w.start,
                        end=w.end,
                        speaker=name_map.get(w.speaker, w.speaker),
                        confidence=w.confidence,
                        sentence_end=w.sentence_end,
                    )
                    for w in transcript.words
                ]
                return Transcript(words=new_words, title=transcript.title, source_duration=transcript.source_duration)
    except Exception as e:
        logger.warning(f"Name refinement non-critical error: {e}")

    return transcript


def _diarize_with_groq_llm(
    transcript: Transcript,
    api_key: str,
    media_path: Path | None = None,
) -> Transcript:
    """
    Segment transcript into speaker turns using Groq LLaMA conversational analysis,
    reinforced with continuous cross-batch dialogue context and acoustic pitch anchoring.
    """
    from groq import Groq

    client = Groq(api_key=api_key, timeout=30.0)
    words = transcript.words

    # 1. Optionally load 16kHz mono audio for pitch ground truth
    audio_data = None
    sr = 16000
    if media_path and Path(media_path).is_file():
        try:
            ffmpeg_bin = find_executable("ffmpeg")
            with tempfile.TemporaryDirectory() as td:
                wav_path = Path(td) / "audio_16k.wav"
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(media_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "wav",
                    str(wav_path),
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                sr, audio_data = scipy.io.wavfile.read(wav_path)
        except Exception as e:
            logger.warning(f"Could not load audio for pitch anchoring: {e}")

    # Group words into sentence-like clauses
    clauses: list[dict[str, Any]] = []
    current_clause_words: list[Word] = []

    for w in words:
        current_clause_words.append(w)
        if w.sentence_end or w.text.endswith((".", "?", "!")) or len(current_clause_words) >= 12:
            s_id = current_clause_words[0].id
            e_id = current_clause_words[-1].id
            txt = " ".join(item.text for item in current_clause_words)
            s_time = current_clause_words[0].start
            e_time = current_clause_words[-1].end
            clauses.append(
                {
                    "start_id": s_id,
                    "end_id": e_id,
                    "start_time": s_time,
                    "end_time": e_time,
                    "text": txt,
                    "pitch": 0.0,
                    "voice_hint": "",
                }
            )
            current_clause_words = []

    if current_clause_words:
        s_id = current_clause_words[0].id
        e_id = current_clause_words[-1].id
        txt = " ".join(item.text for item in current_clause_words)
        s_time = current_clause_words[0].start
        e_time = current_clause_words[-1].end
        clauses.append(
            {
                "start_id": s_id,
                "end_id": e_id,
                "start_time": s_time,
                "end_time": e_time,
                "text": txt,
                "pitch": 0.0,
                "voice_hint": "",
            }
        )

    if not clauses:
        return transcript

    # Estimate pitch per clause if audio is loaded
    if audio_data is not None and len(audio_data) > 0:
        total_audio_samples = len(audio_data)
        for c in clauses:
            s_sample = max(0, int(c["start_time"] * sr))
            e_sample = min(total_audio_samples, int(c["end_time"] * sr))
            if e_sample - s_sample >= int(sr * 0.06):
                p = estimate_voice_pitch(audio_data[s_sample:e_sample], sr)
                c["pitch"] = p
                if 75.0 <= p <= 155.0:
                    c["voice_hint"] = "Low Voice"
                elif 165.0 <= p <= 340.0:
                    c["voice_hint"] = "High Voice"

    batch_size = 50
    all_turns: list[dict[str, Any]] = []
    known_speakers: list[str] = []
    recent_context_turns: list[str] = []

    for i in range(0, len(clauses), batch_size):
        batch = clauses[i : i + batch_size]
        prompt_lines = []
        for c in batch:
            v_hint = f" ({c['voice_hint']})" if c["voice_hint"] else ""
            prompt_lines.append(f"[{c['start_id']}-{c['end_id']}]{v_hint}: {c['text']}")
        prompt_payload = "\n".join(prompt_lines)

        speakers_hint = (
            f"Known speakers in this video: {', '.join(known_speakers)}. Assign each line strictly to one of these speakers ({', '.join(known_speakers)}).\n"
            if known_speakers
            else ""
        )

        history_hint = (
            "Recent dialogue history from previous lines (use this to preserve turn continuity across boundaries):\n"
            + "\n".join(recent_context_turns)
            + "\n\n"
            if recent_context_turns
            else ""
        )

        system_prompt = (
            "You are an expert dialogue speaker diarizer for videos, interviews, and podcasts.\n"
            f"{speakers_hint}"
            f"{history_hint}"
            "Analyze the transcript lines with word ID ranges and assign the correct speaker to each range.\n"
            "- If personal names are introduced or used in the conversation (e.g. 'Vanessa', 'Dan'), use their actual names as speaker labels.\n"
            "- If voice hints are present ('Low Voice' vs 'High Voice'), maintain voice consistency: a male speaker with 'Low Voice' cannot swap with a female speaker with 'High Voice'.\n"
            "- If it is a two-person conversation without names, use 'Speaker 1' and 'Speaker 2'.\n"
            "- If it is a single-person monologue/presentation, label all turns as 'Speaker 1'.\n"
            "Output strict JSON format:\n"
            "{\n"
            '  "speakers": ["Vanessa", "Dan"],\n'
            '  "turns": [\n'
            '    {"start_id": 0, "end_id": 4, "speaker": "Vanessa"},\n'
            '    {"start_id": 5, "end_id": 7, "speaker": "Dan"}\n'
            '  ]\n'
            "}"
        )

        try:
            res = client.chat.completions.create(
                model=DEFAULT_GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_payload},
                ],
                response_format={"type": "json_object"},
                max_tokens=4096,
                temperature=0.0,
            )
            content = res.choices[0].message.content or "{}"
            data = json.loads(content)
            spks = data.get("speakers", [])
            for s in spks:
                s_clean = str(s).strip()
                if s_clean and s_clean not in known_speakers and len(s_clean) <= 20:
                    if s_clean.startswith("Speaker ") and any(not x.startswith("Speaker ") for x in known_speakers):
                        continue
                    known_speakers.append(s_clean)
            turns = data.get("turns", [])
            all_turns.extend(turns)

            # Update recent_context_turns for next batch
            if turns:
                new_recent = []
                for t in turns[-3:]:
                    s_id = int(t.get("start_id", 0))
                    e_id = int(t.get("end_id", s_id))
                    spk_name = str(t.get("speaker", "Speaker 1"))
                    snippet = " ".join(
                        words[idx].text for idx in range(max(0, s_id), min(len(words), e_id + 1))
                    )
                    new_recent.append(f"- {spk_name}: \"{snippet}\"")
                recent_context_turns = new_recent
        except Exception as e:
            logger.warning(f"Groq LLM diarization batch error: {e}")

    if not all_turns:
        return _diarize_with_acoustic_heuristics(transcript)

    name_speakers = [s for s in known_speakers if not s.startswith("Speaker ")]
    fallback_spk = known_speakers[0] if known_speakers else "Speaker 1"

    word_speaker_map: dict[int, str] = {}
    for t in all_turns:
        spk = str(t.get("speaker", fallback_spk)).strip()
        if name_speakers and spk.startswith("Speaker "):
            if spk in ("Speaker 1", "S1") and len(name_speakers) >= 1:
                spk = name_speakers[0]
            elif spk in ("Speaker 2", "S2") and len(name_speakers) >= 2:
                spk = name_speakers[1]
        s_id = int(t.get("start_id", 0))
        e_id = int(t.get("end_id", s_id))
        for wid in range(s_id, e_id + 1):
            word_speaker_map[wid] = spk

    # 4. Acoustic Ground Truth Post-Processing Anchor
    # If two distinct speakers were identified and acoustic pitch was measured:
    if len(known_speakers) == 2 and audio_data is not None:
        spk_a, spk_b = known_speakers[0], known_speakers[1]
        pitches_a = [
            c["pitch"]
            for c in clauses
            if word_speaker_map.get(c["start_id"]) == spk_a and 75.0 <= c["pitch"] <= 340.0
        ]
        pitches_b = [
            c["pitch"]
            for c in clauses
            if word_speaker_map.get(c["start_id"]) == spk_b and 75.0 <= c["pitch"] <= 340.0
        ]

        if len(pitches_a) >= 3 and len(pitches_b) >= 3:
            med_a = float(np.median(pitches_a))
            med_b = float(np.median(pitches_b))
            if abs(med_a - med_b) >= 30.0:
                low_spk = spk_a if med_a < med_b else spk_b
                high_spk = spk_b if med_a < med_b else spk_a
                threshold = (med_a + med_b) / 2.0
                logger.info(
                    f"Acoustic speaker anchor: {low_spk} is Low Voice ({min(med_a, med_b):.1f}Hz), "
                    f"{high_spk} is High Voice ({max(med_a, med_b):.1f}Hz). Threshold: {threshold:.1f}Hz"
                )

                # Reconcile any flipped clauses
                reconciled = 0
                for c in clauses:
                    p = c["pitch"]
                    if 75.0 <= p <= 340.0:
                        cur_assigned = word_speaker_map.get(c["start_id"])
                        if p < threshold - 10.0 and cur_assigned != low_spk:
                            # Clearly lower voice, must be low_spk
                            for wid in range(c["start_id"], c["end_id"] + 1):
                                word_speaker_map[wid] = low_spk
                            reconciled += 1
                        elif p > threshold + 10.0 and cur_assigned != high_spk:
                            # Clearly higher voice, must be high_spk
                            for wid in range(c["start_id"], c["end_id"] + 1):
                                word_speaker_map[wid] = high_spk
                            reconciled += 1
                if reconciled > 0:
                    logger.info(
                        f"Acoustic anchoring reconciled {reconciled} clauses to prevent mid-video identity swap."
                    )

    last_assigned_speaker = fallback_spk
    new_words: list[Word] = []
    for w in words:
        spk = word_speaker_map.get(w.id, last_assigned_speaker)
        last_assigned_speaker = spk
        new_words.append(
            Word(
                id=w.id,
                text=w.text,
                start=w.start,
                end=w.end,
                speaker=spk,
                confidence=w.confidence,
                sentence_end=w.sentence_end,
            )
        )

    logger.info(f"Diarized transcript into speakers: {known_speakers}")
    return Transcript(words=new_words, title=transcript.title, source_duration=transcript.source_duration)


def _diarize_with_acoustic_heuristics(transcript: Transcript) -> Transcript:
    """
    Fallback acoustic pause and turn-taking heuristic.
    A significant pause (>= 1.2s) followed by a question/answer flow alternates speakers.
    """
    words = transcript.words
    current_speaker = "Speaker 1"
    new_words: list[Word] = []

    for i, w in enumerate(words):
        if i > 0:
            prev_w = words[i - 1]
            gap = w.start - prev_w.end

            if gap >= 1.2 and (prev_w.sentence_end or prev_w.text.endswith(("?", "!", "."))):
                current_speaker = "Speaker 2" if current_speaker == "Speaker 1" else "Speaker 1"

        new_words.append(
            Word(
                id=w.id,
                text=w.text,
                start=w.start,
                end=w.end,
                speaker=current_speaker,
                confidence=w.confidence,
                sentence_end=w.sentence_end,
            )
        )

    return Transcript(words=new_words, title=transcript.title, source_duration=transcript.source_duration)
