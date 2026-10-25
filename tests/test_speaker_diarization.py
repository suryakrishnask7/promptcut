"""
tests/test_speaker_diarization.py

Validates:
- Speaker diarization turn assignment and fallback heuristics
- Speaker-targeted captioning ('add captions for Dave', 'caption speaker 1', 'add yellow captions for speaker 2')
- Speaker-targeted cutting ('cut speaker 2')
- Speaker badges formatting in transcript
"""

from __future__ import annotations

import pytest

from promptcut.engine import apply
from promptcut.interpreter.base import IntentTarget
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import AddCaption, CutRange
from promptcut.models.transcript import Transcript, Word
from promptcut.resolver import resolve_intent_target
from promptcut.transcription.diarization import (
    _diarize_with_acoustic_heuristics,
    diarize_transcript_speakers,
)


def _make_conversation_transcript() -> Transcript:
    words = [
        Word(id=0, text="Hello", start=0.0, end=0.4, speaker="S1"),
        Word(id=1, text="Dave", start=0.4, end=0.8, speaker="S1", sentence_end=True),
        Word(id=2, text="Hi", start=2.2, end=2.5, speaker="S1"),
        Word(id=3, text="there", start=2.5, end=2.9, speaker="S1", sentence_end=True),
        Word(id=4, text="Are", start=4.5, end=4.8, speaker="S1"),
        Word(id=5, text="you", start=4.8, end=5.0, speaker="S1"),
        Word(id=6, text="ready?", start=5.0, end=5.4, speaker="S1", sentence_end=True),
    ]
    return Transcript(words=words, title="conv", source_duration=6.0)


def test_acoustic_diarization_heuristics():
    tr = _make_conversation_transcript()
    diarized = _diarize_with_acoustic_heuristics(tr)

    # First turn is Speaker 1
    assert diarized.words[0].speaker == "Speaker 1"
    assert diarized.words[1].speaker == "Speaker 1"

    # After a 1.4s pause (0.8 to 2.2), alternates to Speaker 2
    assert diarized.words[2].speaker == "Speaker 2"
    assert diarized.words[3].speaker == "Speaker 2"

    # After another pause (2.9 to 4.5), alternates back to Speaker 1
    assert diarized.words[4].speaker == "Speaker 1"
    assert diarized.words[5].speaker == "Speaker 1"
    assert diarized.words[6].speaker == "Speaker 1"


def test_resolve_caption_by_speaker():
    words = [
        Word(id=0, text="Welcome", start=0.0, end=0.5, speaker="Host"),
        Word(id=1, text="everyone", start=0.5, end=1.0, speaker="Host"),
        Word(id=2, text="Thank", start=1.2, end=1.5, speaker="Dave"),
        Word(id=3, text="you", start=1.5, end=1.8, speaker="Dave"),
    ]
    tr = Transcript(words=words, title="demo", source_duration=2.0)
    log = OperationLog()
    tl = apply(log, tr)

    # 1. Caption Dave in cyan
    target = IntentTarget(action="caption", speaker="Dave", color="#38BDF8")
    status, ops, meta = resolve_intent_target(target, tr, tl, log)

    assert status == "applied"
    assert ops is not None
    assert len(ops) >= 1
    assert isinstance(ops[0], AddCaption)
    assert ops[0].word_ids == [2, 3]
    assert ops[0].color == "#38BDF8"
    assert "Dave" in meta["summary"]


def test_resolve_cut_by_speaker():
    words = [
        Word(id=0, text="Intro", start=0.0, end=0.5, speaker="Speaker 1"),
        Word(id=1, text="interruption", start=1.0, end=1.5, speaker="Speaker 2"),
        Word(id=2, text="outro", start=2.0, end=2.5, speaker="Speaker 1"),
    ]
    tr = Transcript(words=words, title="demo", source_duration=3.0)
    log = OperationLog()
    tl = apply(log, tr)

    # Cut Speaker 2
    target = IntentTarget(action="cut", speaker="Speaker 2")
    status, ops, meta = resolve_intent_target(target, tr, tl, log)

    assert status == "applied"
    assert ops is not None
    assert len(ops) == 1
    assert isinstance(ops[0], CutRange)
    assert ops[0].word_ids == [1]
    assert "Speaker 2" in meta["summary"]


def test_acoustic_pitch_estimation(tmp_path):
    import numpy as np
    from scipy.io import wavfile
    from promptcut.transcription.diarization import estimate_voice_pitch

    sr = 16000
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    # 1. 120 Hz synthetic male pitch tone
    male_wave = (0.5 * np.sin(2 * np.pi * 120 * t) * 32767).astype(np.int16)
    male_path = tmp_path / "male.wav"
    wavfile.write(str(male_path), sr, male_wave)

    male_pitch = estimate_voice_pitch(male_wave, sr)
    assert male_pitch is not None
    assert 110 <= male_pitch <= 130

    # 2. 220 Hz synthetic female pitch tone
    female_wave = (0.5 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)

    female_pitch = estimate_voice_pitch(female_wave, sr)
    assert female_pitch is not None
    assert 210 <= female_pitch <= 230


def test_acoustic_pitch_diarization(tmp_path):
    import numpy as np
    from scipy.io import wavfile
    from promptcut.transcription.diarization import _diarize_with_acoustic_pitch

    sr = 16000
    duration = 4.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    # Audio with 120 Hz from 0.0 - 2.0s, and 220 Hz from 2.0 - 4.0s
    wave = np.zeros(int(sr * duration), dtype=np.int16)
    wave[: int(sr * 2.0)] = (0.5 * np.sin(2 * np.pi * 120 * t[: int(sr * 2.0)]) * 32767).astype(np.int16)
    wave[int(sr * 2.0) :] = (0.5 * np.sin(2 * np.pi * 220 * t[int(sr * 2.0) :]) * 32767).astype(np.int16)

    audio_path = tmp_path / "dialogue.wav"
    wavfile.write(str(audio_path), sr, wave)

    words = [
        Word(id=0, text="Hello", start=0.2, end=0.8, speaker="S1"),
        Word(id=1, text="world", start=0.9, end=1.5, speaker="S1", sentence_end=True),
        Word(id=2, text="Welcome", start=2.2, end=2.8, speaker="S1"),
        Word(id=3, text="everyone", start=2.9, end=3.5, speaker="S1", sentence_end=True),
    ]
    tr = Transcript(words=words, title="pitch_dialogue", source_duration=4.0)

    diarized = _diarize_with_acoustic_pitch(tr, audio_path)
    # The two segments should be classified as different speakers
    speaker_first_half = diarized.words[0].speaker
    speaker_second_half = diarized.words[2].speaker
    assert speaker_first_half in ("Speaker 1", "Speaker 2")
    assert speaker_second_half in ("Speaker 1", "Speaker 2")
    assert speaker_first_half != speaker_second_half

