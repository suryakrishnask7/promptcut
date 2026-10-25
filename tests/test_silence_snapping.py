"""
tests/test_silence_snapping.py

Unit tests for silence snapping and anti-clipping warnings.
"""

import pytest

from promptcut.engine import apply
from promptcut.media.silence_snapping import snap_cut_to_silence
from promptcut.models.operation_log import OperationLog
from promptcut.models.transcript import Transcript, Word


@pytest.fixture
def sample_timeline():
    # Words:
    # 0: [1.0, 2.0] "Hello"
    # gap: 2.0 - 3.0 (1.0s silence)
    # 1: [3.0, 5.0] "World" (long word)
    # gap: 5.0 - 6.0 (1.0s silence)
    # 2: [6.0, 7.0] "Test"
    words = [
        Word(id=0, text="Hello", start=1.0, end=2.0),
        Word(id=1, text="World", start=3.0, end=5.0),
        Word(id=2, text="Test", start=6.0, end=7.0),
    ]
    t = Transcript(words=words)
    tl = apply(OperationLog(), t)
    return t, tl


def test_cut_already_in_silence(sample_timeline):
    t, tl = sample_timeline
    # Output timeline:
    # Word 0: 0.0 - 1.0
    # Gap: 1.0 - 2.0
    # Word 1: 2.0 - 4.0
    # Cut in the gap 1.2 to 1.8
    snapped = snap_cut_to_silence(t, tl, start_s=1.2, end_s=1.8)
    assert snapped.snapped_start_s == 1.2
    assert snapped.snapped_end_s == 1.8
    assert snapped.target_word_ids == []
    assert len(snapped.warnings) == 0


def test_cut_snaps_to_word_boundary_within_tolerance(sample_timeline):
    t, tl = sample_timeline
    # Word 0 is at 0.0 - 1.0. Cut requested at 0.95s (inside word, 0.05s from end).
    # Tolerance is 0.4s.
    snapped = snap_cut_to_silence(t, tl, start_s=0.95, end_s=1.5, tolerance_s=0.4)
    # Start should snap to 1.0 (word boundary)
    assert snapped.snapped_start_s == 1.0
    assert len(snapped.warnings) == 0


def test_cut_mid_word_outside_tolerance_emits_warning(sample_timeline):
    t, tl = sample_timeline
    # Word 1 is at 2.0 - 4.0 (2 seconds long).
    # Cut requested at 3.0s (exact middle).
    # Distance to start (2.0) is 1.0s > 0.3s tolerance; distance to end (4.0) is 1.0s > 0.3s tolerance.
    snapped = snap_cut_to_silence(t, tl, start_s=3.0, end_s=5.5, tolerance_s=0.3)
    assert any("Audio may be clipped" in w for w in snapped.warnings)
