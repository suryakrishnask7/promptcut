"""
tests/test_log.py

Tests for OperationLog undo/redo/restore semantics (separate from engine tests).
Focuses on the log's own state machine, not on what the engine does with it.
"""

import pytest

from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import CutRange, MuteWords, BleepWords, SpeedRange


def _push(log: OperationLog, *ops) -> OperationLog:
    for op in ops:
        log = log.push(op)
    return log


class TestUndoRedoSemantics:
    """Spec §4: undo/redo move a pointer; a new edit clears redo."""

    def test_undo_one_step(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        log = log.undo()
        assert log.pointer == 1
        assert len(log.active_ops) == 1
        assert log.active_ops[0].op_type == "cut_range"

    def test_undo_all_the_way(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        log = log.undo().undo()
        assert log.pointer == 0
        assert log.active_ops == []

    def test_undo_at_start_is_noop(self):
        log = OperationLog()
        log2 = log.undo()
        assert log2.pointer == 0

    def test_redo_restores_op(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        log = log.undo()
        assert len(log.active_ops) == 1
        log = log.redo()
        assert len(log.active_ops) == 2

    def test_redo_at_end_is_noop(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]))
        log2 = log.redo()
        assert log2.pointer == log.pointer

    def test_new_push_clears_redo(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        log = log.undo()
        assert log.can_redo
        log = log.push(BleepWords(word_ids=[2]))
        assert not log.can_redo
        # The log should now have: [CutRange, BleepWords]
        assert len(log.ops) == 2
        assert log.ops[0].op_type == "cut_range"
        assert log.ops[1].op_type == "bleep_words"

    def test_op_ids_monotone_after_undo_and_new_push(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]))   # op_id=0
        log = _push(log, MuteWords(word_ids=[1]))              # op_id=1
        log = log.undo()
        log = log.push(BleepWords(word_ids=[2]))               # op_id=2 (not 1)
        assert log.ops[-1].op_id == 2

    def test_undo_redo_many_times(self):
        log = _push(OperationLog(),
                    CutRange(word_ids=[0]),
                    MuteWords(word_ids=[1]),
                    BleepWords(word_ids=[2]),
                    SpeedRange(word_ids=[3], factor=2.0))
        for _ in range(4):
            log = log.undo()
        assert log.pointer == 0
        for _ in range(4):
            log = log.redo()
        assert log.pointer == 4
        assert len(log.active_ops) == 4


class TestRestoreOp:
    """Spec §4: restore_op re-activates or inverts a single op without
    disturbing pointer or other ops."""

    def test_restore_deactivates_active_op(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        op0_id = log.ops[0].op_id
        log2 = log.restore_op(op0_id)
        assert log2.ops[0].active is False
        assert log2.ops[1].active is True   # other op untouched
        assert log2.pointer == log.pointer  # pointer unchanged

    def test_restore_reactivates_inactive_op(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]))
        op_id = log.ops[0].op_id
        log2 = log.restore_op(op_id)        # deactivate
        log3 = log2.restore_op(op_id)       # reactivate
        assert log3.ops[0].active is True

    def test_restore_op_not_in_scope_raises(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        log = log.undo()  # pointer=1; ops[1] is out of scope
        op1_id = log.ops[1].op_id
        with pytest.raises(ValueError):
            log.restore_op(op1_id)

    def test_restore_nonexistent_raises(self):
        log = _push(OperationLog(), CutRange(word_ids=[0]))
        with pytest.raises(ValueError):
            log.restore_op(9999)

    def test_restore_does_not_affect_active_ops_list_order(self):
        """After restore_op, active_ops should still be in index order."""
        log = _push(OperationLog(),
                    CutRange(word_ids=[0]),
                    MuteWords(word_ids=[1]),
                    BleepWords(word_ids=[2]))
        log2 = log.restore_op(log.ops[1].op_id)  # deactivate middle op
        active = log2.active_ops
        # Should be [CutRange, BleepWords] in that order
        assert [op.op_type for op in active] == ["cut_range", "bleep_words"]


class TestDuplicateOpSafety:
    """Spec requirement: duplicate destructive op sent twice is safe."""

    def test_has_equivalent_blocks_duplicate(self):
        log = _push(OperationLog(), CutRange(word_ids=[1, 2, 3]))
        assert log.has_equivalent(CutRange(word_ids=[1, 2, 3]))

    def test_different_word_ids_not_equivalent(self):
        log = _push(OperationLog(), CutRange(word_ids=[1, 2]))
        assert not log.has_equivalent(CutRange(word_ids=[1, 3]))

    def test_different_op_type_not_equivalent(self):
        log = _push(OperationLog(), CutRange(word_ids=[1]))
        assert not log.has_equivalent(MuteWords(word_ids=[1]))


class TestJsonRoundTrip:
    """Log must survive JSON serialisation with pointer and op_ids intact."""

    def test_roundtrip_preserves_ops(self):
        log = _push(OperationLog(),
                    CutRange(word_ids=[0, 1]),
                    SpeedRange(word_ids=[2], factor=1.5))
        log_json = log.model_dump_json()
        log2 = OperationLog.model_validate_json(log_json)
        assert log2.pointer == log.pointer
        assert len(log2.ops) == len(log.ops)
        assert log2.ops[0].op_id == 0
        assert log2.ops[1].op_id == 1

    def test_roundtrip_next_id_continues(self):
        """After deserialising, push() should assign the next id correctly."""
        log = _push(OperationLog(), CutRange(word_ids=[0]), MuteWords(word_ids=[1]))
        log2 = OperationLog.model_validate_json(log.model_dump_json())
        log3 = log2.push(BleepWords(word_ids=[2]))
        assert log3.ops[-1].op_id == 2
