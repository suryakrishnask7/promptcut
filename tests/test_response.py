"""
tests/test_response.py

Tests for the three-way response types (Applied, NeedsClarification, Rejected)
and the rules that determine which one to return.

The response layer is intentionally thin – it wraps the engine and resolver.
These tests verify the JSON shape and that time_policy is always present.
"""

import pytest

from promptcut.config import TIME_POLICY
from promptcut.response import (
    Applied,
    ClarificationOption,
    DurationSummary,
    NeedsClarification,
    OpSummary,
    Rejected,
)


class TestApplied:
    def test_basic_construction(self):
        r = Applied(
            operations=[OpSummary(op_id=0, op_type="cut_range", description="Cut 3 words")],
            duration=DurationSummary(before_secs=180.0, after_secs=175.0),
            summary="Removed 3 words (5.0 s).",
        )
        assert r.time_policy == TIME_POLICY
        assert r.warnings == []

    def test_duration_delta(self):
        dur = DurationSummary(before_secs=180.0, after_secs=170.0)
        assert dur.delta_secs == pytest.approx(-10.0)

    def test_json_roundtrip(self):
        r = Applied(
            operations=[],
            duration=DurationSummary(before_secs=60.0, after_secs=55.0),
            summary="No ops.",
        )
        r2 = Applied.model_validate_json(r.model_dump_json())
        assert r2.time_policy == TIME_POLICY
        assert r2.summary == "No ops."


class TestNeedsClarification:
    def test_construction(self):
        r = NeedsClarification(
            question='Did you mean "pricing" at 1:20 or at 2:05?',
            options=[
                ClarificationOption(label="1:20", word_ids=[42], context="...about pricing..."),
                ClarificationOption(label="2:05", word_ids=[88], context="...pricing for..."),
            ],
        )
        assert r.time_policy == TIME_POLICY
        assert len(r.options) == 2

    def test_json_roundtrip(self):
        r = NeedsClarification(
            question="Which one?",
            options=[ClarificationOption(label="A", word_ids=[1])],
        )
        r2 = NeedsClarification.model_validate_json(r.model_dump_json())
        assert r2.options[0].label == "A"


class TestRejected:
    def test_basic_construction(self):
        r = Rejected(reason="Time 5:00 is beyond the video length (3:02).")
        assert r.time_policy == TIME_POLICY
        assert r.conflicting_op_id is None

    def test_with_conflicting_op(self):
        r = Rejected(reason="Conflicts with op 3.", conflicting_op_id=3)
        assert r.conflicting_op_id == 3

    def test_json_roundtrip(self):
        r = Rejected(reason="Out of range.", conflicting_op_id=7)
        r2 = Rejected.model_validate_json(r.model_dump_json())
        assert r2.conflicting_op_id == 7
        assert r2.time_policy == TIME_POLICY


class TestTimePolicyAlwaysPresent:
    """Every response type must carry time_policy in its JSON output."""

    def test_applied_has_policy(self):
        d = Applied(
            operations=[],
            duration=DurationSummary(before_secs=10.0, after_secs=8.0),
            summary="x",
        ).model_dump()
        assert "time_policy" in d
        assert d["time_policy"] == TIME_POLICY

    def test_clarification_has_policy(self):
        d = NeedsClarification(
            question="?",
            options=[ClarificationOption(label="A", word_ids=[0])],
        ).model_dump()
        assert d["time_policy"] == TIME_POLICY

    def test_rejected_has_policy(self):
        d = Rejected(reason="no").model_dump()
        assert d["time_policy"] == TIME_POLICY
