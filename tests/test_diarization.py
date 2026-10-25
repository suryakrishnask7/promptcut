"""
tests/test_diarization.py

Unit tests for speaker diarization sidecar adapter.
"""

import json
from pathlib import Path
import pytest

from promptcut.diarization import MockSidecarDiarizationAdapter
from promptcut.models.transcript import Transcript, Word


@pytest.fixture
def base_transcript() -> Transcript:
    words = [
        Word(id=0, text="Hello", start=0.0, end=1.0, speaker="S1"),
        Word(id=1, text="World", start=1.2, end=2.0, speaker="S1"),
        Word(id=2, text="Guest", start=3.0, end=4.0, speaker="S1"),
        Word(id=3, text="Speaking", start=4.2, end=5.0, speaker="S1"),
    ]
    return Transcript(words=words)


def test_sidecar_intervals_mapping(base_transcript: Transcript, tmp_path: Path):
    sidecar_file = tmp_path / "sidecar.json"
    sidecar_data = {
        "intervals": [
            {"start": 0.0, "end": 2.5, "speaker": "Host"},
            {"start": 2.5, "end": 6.0, "speaker": "Guest"},
        ]
    }
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    adapter = MockSidecarDiarizationAdapter()
    annotated = adapter.apply_speakers(base_transcript, sidecar_file)

    assert annotated.words[0].speaker == "Host"
    assert annotated.words[1].speaker == "Host"
    assert annotated.words[2].speaker == "Guest"
    assert annotated.words[3].speaker == "Guest"


def test_sidecar_direct_word_map(base_transcript: Transcript, tmp_path: Path):
    sidecar_file = tmp_path / "word_sidecar.json"
    sidecar_data = {
        "word_speakers": {
            "2": "Alice",
            "3": "Alice",
        }
    }
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    adapter = MockSidecarDiarizationAdapter()
    annotated = adapter.apply_speakers(base_transcript, sidecar_file)

    assert annotated.words[0].speaker == "S1"
    assert annotated.words[1].speaker == "S1"
    assert annotated.words[2].speaker == "Alice"
    assert annotated.words[3].speaker == "Alice"


def test_sidecar_none_returns_unchanged(base_transcript: Transcript):
    adapter = MockSidecarDiarizationAdapter()
    same = adapter.apply_speakers(base_transcript, None)
    assert same == base_transcript


def test_sidecar_file_not_found(base_transcript: Transcript):
    adapter = MockSidecarDiarizationAdapter()
    with pytest.raises(FileNotFoundError):
        adapter.apply_speakers(base_transcript, "non_existent_sidecar.json")
