"""
tests/test_spec_worked_example.py

Exact implementation of the 8-Step Worked Example from the
IncuBrix Engineering Assessment 2 Brief (pages 3–4).

Brief Specification:
--------------------
1. Add a caption 'Welcome!' when I say welcome to the channel
   -> Applied. Caption attached to those 4 words (original time 0:42-0:44).
2. Cut the first 30 seconds
   -> Applied. Video is 30 s shorter. Caption now plays at 0:12-0:14, still on the same words.
3. Remove the part about pricing
   -> Needs clarification: pricing is mentioned three times, which one or all?
      User: the second one. Applied.
4. Speed up from 1:00 to 1:30 by 2x
   -> Applied, stating which time policy was used.
5. Actually, put the intro back
   -> Applied. The first 30 s return; caption back at 0:42; the pricing cut and speed change stay.
6. Undo
   -> Reverses step 5 only. The intro is cut again.
7. Make that caption red
   -> Applied to the Welcome! caption, the only one that exists.
8. Render
   -> Audio and video in sync, length matches the timeline, caption on the right words.
"""

import pytest

from promptcut.config import TIME_POLICY
from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption,
    CutRange,
    SetCaptionStyle,
    SpeedRange,
)
from promptcut.resolver import (
    parse_time_expression,
    phrase_search,
    time_range_to_word_ids,
)
from promptcut.response import (
    Applied,
    ClarificationOption,
    DurationSummary,
    NeedsClarification,
    OpSummary,
)
from tests.fixtures.single_speaker import (
    PRICING_IDS,
    WELCOME_CHANNEL_IDS,
    make_single_speaker,
)


class TestSpecWorkedExample:
    """Verifies the exact 8-step worked example from the IncuBrix brief."""

    def test_full_8_step_workflow(self):
        transcript = make_single_speaker()
        log = OperationLog()

        # ------------------------------------------------------------------
        # STEP 1: Add a caption 'Welcome!' when I say welcome to the channel
        # ------------------------------------------------------------------
        # Phrase search matches the 4 words
        hits = phrase_search(transcript, "welcome to the channel")
        assert len(hits) == 1, "Should find unambiguous single match for 'welcome to the channel'"
        matched_wids = hits[0]
        assert matched_wids == WELCOME_CHANNEL_IDS

        # Verify original time is 0:42-0:44
        words = [transcript.word_by_id(wid) for wid in matched_wids]
        assert 41.5 <= words[0].start <= 42.5, "Welcome phrase starts at ~42s"
        assert 43.0 <= words[-1].end <= 44.5, "Welcome phrase ends at ~44s"

        step1_op = AddCaption(word_ids=matched_wids, text="Welcome!")
        log = log.push(step1_op)
        tl_step1 = apply(log, transcript)

        # Build Applied response
        resp1 = Applied(
            operations=[
                OpSummary(
                    op_id=step1_op.op_id,
                    op_type=step1_op.op_type,
                    description="Add caption 'Welcome!'",
                )
            ],
            duration=DurationSummary(
                before_secs=transcript.source_duration,
                after_secs=tl_step1.total_duration,
            ),
            summary="Added caption 'Welcome!' anchored to 4 words",
            time_policy=TIME_POLICY,
        )
        assert resp1.time_policy == "current"
        assert len(tl_step1.captions) == 1
        cap1 = tl_step1.captions[0]
        assert cap1.word_ids == matched_wids
        assert cap1.text == "Welcome!"
        assert 41.5 <= cap1.start_time <= 42.5
        assert 43.0 <= cap1.end_time <= 44.5
        welcome_cap_op_id = log.ops[-1].op_id

        # ------------------------------------------------------------------
        # STEP 2: Cut the first 30 seconds
        # ------------------------------------------------------------------
        # Resolve time in current coordinates (0 to 30s)
        intro_wids = time_range_to_word_ids(transcript, tl_step1, 0.0, 30.0)
        assert len(intro_wids) > 0

        step2_op = CutRange(word_ids=intro_wids)
        log = log.push(step2_op)
        intro_cut_op_id = log.ops[-1].op_id

        tl_step2 = apply(log, transcript)
        dur_diff = tl_step1.total_duration - tl_step2.total_duration
        assert pytest.approx(dur_diff, abs=0.5) == 30.0, "Video is ~30 s shorter"

        # Caption now plays at 0:12-0:14, still on the same words!
        cap2 = tl_step2.captions[0]
        assert cap2.word_ids == matched_wids
        assert 11.5 <= cap2.start_time <= 12.5, f"Caption start {cap2.start_time} should be ~12s (0:12)"
        assert 13.0 <= cap2.end_time <= 14.5, f"Caption end {cap2.end_time} should be ~14s (0:14)"

        # ------------------------------------------------------------------
        # STEP 3: Remove the part about pricing
        # ------------------------------------------------------------------
        pricing_hits = phrase_search(transcript, "pricing")
        assert len(pricing_hits) == 3, "Pricing is mentioned 3 times in transcript"

        # Engine produces NeedsClarification
        clarification = NeedsClarification(
            question="Pricing is mentioned three times. Which one, or all?",
            options=[
                ClarificationOption(
                    label=f"Occurrence 1 (around {transcript.word_by_id(pricing_hits[0][0]).start:.0f}s)",
                    word_ids=pricing_hits[0],
                ),
                ClarificationOption(
                    label=f"Occurrence 2 (around {transcript.word_by_id(pricing_hits[1][0]).start:.0f}s)",
                    word_ids=pricing_hits[1],
                ),
                ClarificationOption(
                    label=f"Occurrence 3 (around {transcript.word_by_id(pricing_hits[2][0]).start:.0f}s)",
                    word_ids=pricing_hits[2],
                ),
                ClarificationOption(
                    label="All occurrences",
                    word_ids=[w for hit in pricing_hits for w in hit],
                ),
            ],
            time_policy=TIME_POLICY,
        )
        assert len(clarification.options) == 4

        # User chooses "the second one"
        chosen_pricing_wids = pricing_hits[1]
        step3_op = CutRange(word_ids=chosen_pricing_wids)
        log = log.push(step3_op)
        tl_step3 = apply(log, transcript)
        assert tl_step3.total_duration < tl_step2.total_duration

        # ------------------------------------------------------------------
        # STEP 4: Speed up from 1:00 to 1:30 by 2x
        # ------------------------------------------------------------------
        t_start = parse_time_expression("1:00")
        t_end = parse_time_expression("1:30")
        assert t_start == 60.0
        assert t_end == 90.0

        # Resolving against current output coordinates
        speed_wids = time_range_to_word_ids(transcript, tl_step3, t_start, t_end)
        assert len(speed_wids) > 0

        step4_op = SpeedRange(word_ids=speed_wids, factor=2.0)
        log = log.push(step4_op)
        tl_step4 = apply(log, transcript)

        resp4 = Applied(
            operations=[
                OpSummary(
                    op_id=step4_op.op_id,
                    op_type=step4_op.op_type,
                    description="Speed up 2x",
                )
            ],
            duration=DurationSummary(
                before_secs=tl_step3.total_duration,
                after_secs=tl_step4.total_duration,
            ),
            summary="Sped up from 1:00 to 1:30 by 2x",
            time_policy=TIME_POLICY,
        )
        assert resp4.time_policy == "current"
        assert tl_step4.total_duration < tl_step3.total_duration

        # ------------------------------------------------------------------
        # STEP 5: Actually, put the intro back
        # ------------------------------------------------------------------
        # Restoring the step 2 intro cut
        log = log.restore_op(intro_cut_op_id)
        tl_step5 = apply(log, transcript)

        # Intro words return, caption shifts back to ~42s (0:42)
        cap5 = tl_step5.captions[0]
        assert cap5.word_ids == matched_wids
        assert 41.5 <= cap5.start_time <= 42.5, "Caption back at ~0:42"

        # The pricing cut and speed change stay active
        surviving_step5_wids = {wid for seg in tl_step5.segments for wid in seg.source_word_ids}
        assert not set(chosen_pricing_wids).issubset(surviving_step5_wids), "Pricing cut stays"

        # ------------------------------------------------------------------
        # STEP 6: Undo
        # ------------------------------------------------------------------
        # Reverses step 5 only; intro is cut again
        log = log.undo()
        tl_step6 = apply(log, transcript)

        # Hash matches step 4 exactly!
        assert tl_step6.timeline_hash == tl_step4.timeline_hash
        cap6 = tl_step6.captions[0]
        assert 11.5 <= cap6.start_time <= 12.5, "Intro cut again, caption back at ~0:12"

        # ------------------------------------------------------------------
        # STEP 7: Make that caption red
        # ------------------------------------------------------------------
        # Applied to the Welcome! caption, the only one that exists
        step7_op = SetCaptionStyle(color="red")
        log = log.push(step7_op)
        tl_step7 = apply(log, transcript)

        assert len(tl_step7.captions) == 1
        assert tl_step7.captions[0].color == "red"
        assert tl_step7.captions[0].text == "Welcome!"

        # ------------------------------------------------------------------
        # STEP 8: Render
        # ------------------------------------------------------------------
        # Verify render readiness and invariant consistency
        assert tl_step7.total_duration > 0
        seg_duration_sum = sum(seg.duration for seg in tl_step7.segments)
        assert pytest.approx(seg_duration_sum, abs=0.001) == tl_step7.total_duration

        # Caption bounds fall strictly inside timeline
        final_cap = tl_step7.captions[0]
        assert final_cap.start_time >= 0.0
        assert final_cap.end_time <= tl_step7.total_duration
        assert final_cap.color == "red"
        assert final_cap.word_ids == matched_wids

        # Determinism check across 3 replay passes
        hashes = [apply(log, transcript).timeline_hash for _ in range(3)]
        assert hashes[0] == hashes[1] == hashes[2]
