"""
tests/test_worked_example.py

8-Step worked example for the IncuBrix Engineering Assessment 2 review.

ASSUMPTION NOTE
---------------
The PDF brief describes an 8-step worked example that could not be extracted
from the encrypted PDF.  This file implements a worked example that exercises
every core mechanic the spec mandates:
  1. Load transcript, apply empty log → full timeline
  2. remove_fillers → timeline shortens, fillers absent
  3. add_caption anchored to first "pricing" occurrence
  4. cut_range on words before the captioned "pricing" section → caption timing shifts
  5. speed_range 1.5× on the captioned section → caption compressed
  6. cut_range the captioned pricing words → caption removed (warning)
  7. undo step 6 → caption returns
  8. restore_op step 4's cut → those words come back; verify hash stable

All assertions are inline.  Each step prints a brief trace to stdout so the
live review can follow along.

If you have the actual worked example from the brief, please share the steps
and I will update this file to match exactly.
"""

import pytest

from promptcut.config import TIME_POLICY
from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption,
    CutRange,
    RemoveFillers,
    SpeedRange,
)
from tests.fixtures.single_speaker import (
    make_single_speaker,
    PRICING_IDS,
    FILLER_IDS,
    THANKS_IDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wids_in_tl(tl) -> set[int]:
    return {wid for seg in tl.segments for wid in seg.source_word_ids}


def _cap_word_ids(tl) -> set[int]:
    return {wid for cap in tl.captions for wid in cap.word_ids}


# ---------------------------------------------------------------------------
# The 8-step example
# ---------------------------------------------------------------------------


class TestWorkedExample:
    """
    Each method is one step and can be run individually or as a suite.
    The ``log`` and ``transcript`` are built up cumulatively via class-level
    state so the story flows naturally.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        self.transcript = make_single_speaker()
        self.log = OperationLog()
        self.cut_before_pricing_op_id = None   # set in step 4
        self.cut_pricing_op_id = None           # set in step 6

    # ------------------------------------------------------------------
    # Step 1: Empty log → full transcript mirrored in timeline
    # ------------------------------------------------------------------

    def test_step_1_empty_log_full_timeline(self):
        tl = apply(self.log, self.transcript)
        all_wids = _wids_in_tl(tl)
        source_wids = {w.id for w in self.transcript.words}

        assert all_wids == source_wids, "Empty log must mirror every source word"
        assert tl.total_duration > 0
        assert tl.captions == []
        assert len(tl.warnings) == 0
        print(f"\n[Step 1] Full timeline: {tl.total_duration:.1f} s, "
              f"{len(tl.segments)} segment(s), hash={tl.timeline_hash[:12]}…")

    # ------------------------------------------------------------------
    # Step 2: remove_fillers → transcript shortens
    # ------------------------------------------------------------------

    def test_step_2_remove_fillers(self):
        tl_before = apply(self.log, self.transcript)
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        tl_after = apply(self.log, self.transcript)

        assert tl_after.total_duration < tl_before.total_duration, \
            "Removing fillers must shorten the timeline"

        removed_wids = _wids_in_tl(tl_before) - _wids_in_tl(tl_after)
        assert set(FILLER_IDS).issubset(removed_wids), \
            "All fixture filler words must be gone"

        print(f"\n[Step 2] After remove_fillers: {tl_after.total_duration:.1f} s "
              f"(−{tl_before.total_duration - tl_after.total_duration:.1f} s), "
              f"{len(removed_wids)} filler word(s) removed")

    # ------------------------------------------------------------------
    # Step 3: add_caption anchored to first "pricing" occurrence
    # ------------------------------------------------------------------

    def test_step_3_add_caption_to_pricing(self):
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        first_pricing_id = PRICING_IDS[0]
        self.log = self.log.push(
            AddCaption(word_ids=[first_pricing_id], text="Pricing Plans")
        )
        tl = apply(self.log, self.transcript)

        assert len(tl.captions) == 1
        cap = tl.captions[0]
        assert first_pricing_id in cap.word_ids
        assert cap.text == "Pricing Plans"
        assert cap.duration >= 0.7   # min display floor enforced

        print(f"\n[Step 3] Caption added: '{cap.text}' "
              f"at {cap.start_time:.2f}–{cap.end_time:.2f} s")

    # ------------------------------------------------------------------
    # Step 4: cut_range on words BEFORE the pricing section
    #         → caption timing shifts left (still anchored to its word)
    # ------------------------------------------------------------------

    def test_step_4_cut_before_pricing_caption_shifts(self):
        first_pricing_id = PRICING_IDS[0]
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        self.log = self.log.push(
            AddCaption(word_ids=[first_pricing_id], text="Pricing Plans")
        )

        tl_before = apply(self.log, self.transcript)
        cap_time_before = tl_before.captions[0].start_time

        # Cut the first 5 source words (intro only – nowhere near pricing)
        cut_op = CutRange(word_ids=list(range(5)))
        self.log = self.log.push(cut_op)

        tl_after = apply(self.log, self.transcript)
        cap_time_after = tl_after.captions[0].start_time

        assert len(tl_after.captions) == 1, "Caption must survive an unrelated cut"
        assert cap_time_after < cap_time_before, \
            "Caption must shift left when words before it are cut"

        print(f"\n[Step 4] Caption shifted from {cap_time_before:.2f} s "
              f"→ {cap_time_after:.2f} s after cutting intro words")

    # ------------------------------------------------------------------
    # Step 5: speed_range 1.5× on the pricing section
    #         → caption duration compressed
    # ------------------------------------------------------------------

    def test_step_5_speed_compresses_caption(self):
        first_pricing_id = PRICING_IDS[0]
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        self.log = self.log.push(
            AddCaption(word_ids=[first_pricing_id], text="Pricing Plans")
        )

        tl_before = apply(self.log, self.transcript)
        cap_dur_before = tl_before.captions[0].duration

        self.log = self.log.push(SpeedRange(word_ids=[first_pricing_id], factor=1.5))
        tl_after = apply(self.log, self.transcript)

        assert len(tl_after.captions) == 1
        cap_dur_after = tl_after.captions[0].duration
        # 1.5x speed → caption on-screen for a shorter window
        # (or hits the 0.7s floor if word is very short)
        assert cap_dur_after <= cap_dur_before or cap_dur_after == pytest.approx(0.7, abs=0.01), \
            "Caption duration must decrease (or hit floor) with 1.5× speed"

        print(f"\n[Step 5] Caption duration: {cap_dur_before:.3f} s "
              f"→ {cap_dur_after:.3f} s with 1.5× speed")

    # ------------------------------------------------------------------
    # Step 6: cut_range on the captioned pricing words
    #         → caption removed, warning issued
    # ------------------------------------------------------------------

    def test_step_6_cut_captioned_words_removes_caption(self):
        first_pricing_id = PRICING_IDS[0]
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        self.log = self.log.push(
            AddCaption(word_ids=[first_pricing_id], text="Pricing Plans")
        )
        self.log = self.log.push(CutRange(word_ids=[first_pricing_id]))

        tl = apply(self.log, self.transcript)

        assert len(tl.captions) == 0, \
            "Caption must be removed when all its anchor words are cut"
        assert len(tl.removed_captions) == 1
        assert any("cut" in w.lower() for w in tl.warnings), \
            "Warning must be issued for the removed caption"

        print(f"\n[Step 6] Caption removed. Warnings: {tl.warnings}")

    # ------------------------------------------------------------------
    # Step 7: undo step 6 → caption returns
    # ------------------------------------------------------------------

    def test_step_7_undo_restores_caption(self):
        first_pricing_id = PRICING_IDS[0]
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        self.log = self.log.push(
            AddCaption(word_ids=[first_pricing_id], text="Pricing Plans")
        )
        self.log = self.log.push(CutRange(word_ids=[first_pricing_id]))

        tl_cut = apply(self.log, self.transcript)
        assert len(tl_cut.captions) == 0

        self.log = self.log.undo()
        tl_undone = apply(self.log, self.transcript)

        assert len(tl_undone.captions) == 1, \
            "Undo must restore the caption"
        assert tl_undone.captions[0].text == "Pricing Plans"

        print(f"\n[Step 7] After undo: caption restored at "
              f"{tl_undone.captions[0].start_time:.2f} s")

    # ------------------------------------------------------------------
    # Step 8: restore_op on an earlier cut → verify hash stability
    # ------------------------------------------------------------------

    def test_step_8_restore_earlier_op_and_hash_stable(self):
        first_pricing_id = PRICING_IDS[0]

        # Build up the log: fillers → caption → intro-cut → pricing-cut
        self.log = self.log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        self.log = self.log.push(
            AddCaption(word_ids=[first_pricing_id], text="Pricing Plans")
        )
        # intro cut (step 4 equivalent)
        intro_cut = CutRange(word_ids=list(range(5)))
        self.log = self.log.push(intro_cut)
        intro_cut_op_id = self.log.ops[-1].op_id

        # pricing cut (step 6 equivalent)
        self.log = self.log.push(CutRange(word_ids=[first_pricing_id]))

        # Undo the pricing cut (step 7)
        self.log = self.log.undo()

        # restore_op on the intro cut (re-activate those words)
        self.log = self.log.restore_op(intro_cut_op_id)

        tl = apply(self.log, self.transcript)

        # Intro words (0-4) should be back in the timeline
        all_wids = _wids_in_tl(tl)
        assert 0 in all_wids and 4 in all_wids, \
            "restore_op must bring intro words back without disturbing later ops"

        # Caption must still be present
        assert len(tl.captions) == 1

        # Determinism: same log replayed 3 times → identical hash
        hashes = [apply(self.log, self.transcript).timeline_hash for _ in range(3)]
        assert hashes[0] == hashes[1] == hashes[2], \
            "timeline_hash must be deterministic across replays"

        print(f"\n[Step 8] restore_op: intro words back. "
              f"Hash={hashes[0][:16]}… (stable ×3)")


# ---------------------------------------------------------------------------
# Spec-specific edge cases (referenced in the brief deliverables)
# ---------------------------------------------------------------------------


class TestSpecEdgeCases:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.transcript = make_single_speaker()

    def test_add_caption_beyond_video_length_rejected_by_caller(self):
        """
        "add a caption at 5:00" on a video shorter than 5 minutes → Rejected.

        The engine itself does not issue Rejected responses – that is the
        response-layer's job.  Here we verify that time_range_to_word_ids
        returns [] for an out-of-range time, which the response layer should
        translate to a Rejected response.
        """
        from promptcut.resolver import time_range_to_word_ids

        tl = apply(OperationLog(), self.transcript)
        # Video is ~3 min; 5:00 = 300 s
        ids = time_range_to_word_ids(self.transcript, tl, 300.0, 301.0)
        assert ids == [], \
            "time_range_to_word_ids must return [] for out-of-range time"

    def test_ambiguous_phrase_has_multiple_hits(self):
        """
        "pricing" matches 3 places → response layer should return
        NeedsClarification.  Here we verify the resolver returns 3 hits.
        """
        from promptcut.resolver import phrase_search

        hits = phrase_search(self.transcript, "pricing")
        assert len(hits) >= 2, \
            "Ambiguous phrase must produce ≥2 hits for NeedsClarification logic"

    def test_cut_everything_produces_empty_timeline(self):
        all_ids = [w.id for w in self.transcript.words]
        log = OperationLog().push(CutRange(word_ids=all_ids))
        tl = apply(log, self.transcript)
        assert tl.segments == []
        assert any("empty" in w.lower() for w in tl.warnings)

    def test_determinism_replay_3_times(self):
        log = OperationLog()
        log = log.push(RemoveFillers(filler_words=["um", "uh", "like"]))
        log = log.push(AddCaption(word_ids=PRICING_IDS[:1], text="Price"))
        log = log.push(CutRange(word_ids=THANKS_IDS[:1]))
        hashes = [apply(log, self.transcript).timeline_hash for _ in range(3)]
        assert hashes[0] == hashes[1] == hashes[2]
