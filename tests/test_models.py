"""
tests/test_models.py

Unit tests for data model validation rules (not the engine).
"""

import pytest
from pydantic import ValidationError

from promptcut.models.operations import (
    AddCaption, AddMusicDucked, AutoCaption, BleepWords, CutRange,
    KeepRange, MuteWords, RemoveFillers, RemoveSilences, ReorderSections,
    SpeedRange,
)
from promptcut.models.operation_log import OperationLog
from promptcut.models.transcript import Transcript, Word


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

class TestWord:
    def test_basic(self):
        w = Word(id=0, text="hello", start=0.0, end=0.5)
        assert w.speaker == "S1"
        assert w.confidence == 1.0

    def test_end_before_start_raises(self):
        with pytest.raises(ValidationError):
            Word(id=0, text="x", start=1.0, end=0.5)

    def test_end_equals_start_ok(self):
        # Zero-duration word is allowed (e.g. a punctuation marker)
        w = Word(id=0, text=".", start=1.0, end=1.0)
        assert w.end == w.start


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

class TestTranscript:
    def _make(self, n: int = 3) -> Transcript:
        words = [Word(id=i, text=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4) for i in range(n)]
        return Transcript(words=words)

    def test_ids_must_be_sequential(self):
        words = [
            Word(id=0, text="a", start=0.0, end=0.3),
            Word(id=2, text="b", start=0.5, end=0.8),  # gap in IDs
        ]
        with pytest.raises(ValidationError):
            Transcript(words=words)

    def test_source_duration_inferred(self):
        t = self._make(3)
        assert t.source_duration == pytest.approx(2 * 0.5 + 0.4)

    def test_word_by_id_ok(self):
        t = self._make(5)
        assert t.word_by_id(3).text == "w3"

    def test_word_by_id_oob(self):
        t = self._make(3)
        assert t.word_by_id(99) is None

    def test_original_positions(self):
        t = self._make(4)
        pos = t.original_positions()
        assert pos[0] == 0
        assert pos[3] == 3


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

class TestOperationModels:
    def test_cut_range_requires_word_ids(self):
        with pytest.raises(ValidationError):
            CutRange(word_ids=[])

    def test_speed_range_factor_positive(self):
        with pytest.raises(ValidationError):
            SpeedRange(word_ids=[1], factor=0.0)
        with pytest.raises(ValidationError):
            SpeedRange(word_ids=[1], factor=-1.0)

    def test_speed_range_ok(self):
        op = SpeedRange(word_ids=[1, 2], factor=1.5)
        assert op.op_type == "speed_range"
        assert op.op_id == -1  # unassigned sentinel

    def test_add_caption_defaults(self):
        op = AddCaption(word_ids=[1], text="Hello")
        assert op.min_display_secs == 0.7

    def test_add_music_duck_level_le_zero(self):
        with pytest.raises(ValidationError):
            AddMusicDucked(word_ids=[1], track_ref="bg.mp3", duck_level_db=5.0)


# ---------------------------------------------------------------------------
# OperationLog
# ---------------------------------------------------------------------------

class TestOperationLog:
    def test_empty_log(self):
        log = OperationLog()
        assert log.pointer == 0
        assert log.ops == []
        assert log.active_ops == []
        assert not log.can_undo
        assert not log.can_redo

    def test_push_assigns_op_id(self):
        log = OperationLog()
        log2 = log.push(CutRange(word_ids=[0]))
        assert log2.ops[0].op_id == 0
        assert log2.pointer == 1

    def test_push_increments_ids(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log = log.push(MuteWords(word_ids=[1]))
        assert log.ops[1].op_id == 1

    def test_undo_decrements_pointer(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log = log.push(MuteWords(word_ids=[1]))
        log_u = log.undo()
        assert log_u.pointer == 1
        assert len(log_u.active_ops) == 1

    def test_undo_at_zero_noop(self):
        log = OperationLog()
        log2 = log.undo()
        assert log2.pointer == 0

    def test_redo_increments_pointer(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log = log.undo()
        log = log.redo()
        assert log.pointer == 1

    def test_redo_at_end_noop(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log2 = log.redo()
        assert log2.pointer == log.pointer

    def test_push_clears_redo(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log = log.push(MuteWords(word_ids=[1]))
        log = log.undo()
        assert log.can_redo
        log = log.push(BleepWords(word_ids=[2]))
        assert not log.can_redo
        assert len(log.ops) == 2  # first op + new op; second op discarded

    def test_restore_op_toggles_active(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log = log.push(MuteWords(word_ids=[1]))
        op0_id = log.ops[0].op_id
        log2 = log.restore_op(op0_id)
        assert log2.ops[0].active is False
        # toggle back
        log3 = log2.restore_op(op0_id)
        assert log3.ops[0].active is True

    def test_restore_op_out_of_scope_raises(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        log = log.push(MuteWords(word_ids=[1]))
        log = log.undo()  # pointer = 1; ops[1] is out of scope
        op1_id = log.ops[1].op_id
        with pytest.raises(ValueError):
            log.restore_op(op1_id)

    def test_restore_nonexistent_op_raises(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[0]))
        with pytest.raises(ValueError):
            log.restore_op(9999)

    def test_has_equivalent_detects_duplicate(self):
        log = OperationLog()
        op = CutRange(word_ids=[1, 2, 3])
        log = log.push(op)
        assert log.has_equivalent(CutRange(word_ids=[1, 2, 3]))

    def test_has_equivalent_false_for_new(self):
        log = OperationLog()
        log = log.push(CutRange(word_ids=[1]))
        assert not log.has_equivalent(CutRange(word_ids=[2]))

    def test_pointer_cannot_exceed_ops(self):
        import pytest
        with pytest.raises(Exception):
            OperationLog(ops=[], pointer=5)
