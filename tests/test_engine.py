"""
tests/test_engine.py

Tests for the engine.apply() function.

Covers:
  • Empty log → full transcript mirrored in timeline
  • CutRange removes words and adjusts timing
  • KeepRange excludes everything else
  • ReorderSections restructures the timeline
  • SpeedRange speeds up a section (duration reduced)
  • SpeedRange last-write-wins on overlap
  • MuteWords / BleepWords flags propagate to segments
  • RemoveFillers cuts filler words
  • RemoveSilences compresses silence gaps
  • Caption follows its words after cuts (partial + full)
  • Caption follows words after speed changes
  • Caption follows words after reorder
  • Caption fully cut → removed + warning issued
  • Caption partially cut → warning, caption survives with surviving words
  • Caption 0.7 s minimum display enforced
  • AutoCaption derives text from word texts
  • AddMusicDucked is data-only (does not affect segment count)
  • Duplicate cut_range is safe (idempotent)
  • Empty timeline (all words cut) produces empty segments + warning
"""

import pytest

from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import (
    AddCaption, AutoCaption, BleepWords, CutRange, KeepRange,
    MuteWords, ReorderSections, RemoveFillers, RemoveSilences,
    SpeedRange, AddMusicDucked,
)
from promptcut.models.transcript import Transcript, Word
from tests.fixtures.single_speaker import (
    make_single_speaker,
    PRICING_IDS,
    THANKS_IDS,
    FILLER_IDS,
    UM_IDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_transcript(n: int = 10) -> Transcript:
    """Make a compact n-word transcript for engine unit tests."""
    words = [
        Word(id=i, text=f"word{i}", start=i * 0.5, end=i * 0.5 + 0.4)
        for i in range(n)
    ]
    return Transcript(words=words, title="simple")


def _push_ops(ops, log=None):
    if log is None:
        log = OperationLog()
    for op in ops:
        log = log.push(op)
    return log


# ---------------------------------------------------------------------------
# Basic empty-log behaviour
# ---------------------------------------------------------------------------

class TestEmptyLog:
    def test_empty_log_mirrors_transcript(self):
        t = _simple_transcript(5)
        tl = apply(OperationLog(), t)
        all_wids = [wid for seg in tl.segments for wid in seg.source_word_ids]
        assert all_wids == [0, 1, 2, 3, 4]

    def test_total_duration_matches_source(self):
        t = _simple_transcript(5)
        tl = apply(OperationLog(), t)
        # source: 5 words × (0.4 duration + 0.1 gap) - last gap = 5 × 0.5 - 0.1 = 2.4
        # Actually words are 0-0.4, 0.5-0.9, 1.0-1.4, 1.5-1.9, 2.0-2.4
        assert tl.total_duration == pytest.approx(2.4, abs=0.01)

    def test_single_segment_for_uniform_source(self):
        t = _simple_transcript(5)
        tl = apply(OperationLog(), t)
        assert len(tl.segments) == 1

    def test_hash_is_non_empty_string(self):
        t = _simple_transcript(3)
        tl = apply(OperationLog(), t)
        assert isinstance(tl.timeline_hash, str)
        assert len(tl.timeline_hash) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# CutRange
# ---------------------------------------------------------------------------

class TestCutRange:
    def test_cut_reduces_segment_count(self):
        t = _simple_transcript(6)
        # Cut words 2 and 3 → splits at [0,1] [4,5]
        log = _push_ops([CutRange(word_ids=[2, 3])])
        tl = apply(log, t)
        all_wids = [wid for seg in tl.segments for wid in seg.source_word_ids]
        assert 2 not in all_wids
        assert 3 not in all_wids
        assert 0 in all_wids and 5 in all_wids

    def test_cut_produces_two_segments(self):
        t = _simple_transcript(6)
        log = _push_ops([CutRange(word_ids=[2, 3])])
        tl = apply(log, t)
        assert len(tl.segments) == 2

    def test_cut_adjusts_timing(self):
        t = _simple_transcript(6)
        log = _push_ops([CutRange(word_ids=[0, 1, 2])])
        tl = apply(log, t)
        # Remaining words 3-5 should start at output time 0
        assert tl.segments[0].start_time == pytest.approx(0.0)

    def test_cut_everything_returns_empty_timeline(self):
        t = _simple_transcript(3)
        log = _push_ops([CutRange(word_ids=[0, 1, 2])])
        tl = apply(log, t)
        assert tl.segments == []
        assert any("empty" in w.lower() for w in tl.warnings)

    def test_duplicate_cut_is_safe(self):
        """Sending the same cut twice should not corrupt the timeline."""
        t = _simple_transcript(5)
        log = _push_ops([
            CutRange(word_ids=[2]),
            CutRange(word_ids=[2]),  # duplicate
        ])
        tl = apply(log, t)
        all_wids = [w for seg in tl.segments for w in seg.source_word_ids]
        assert 2 not in all_wids
        # Words 0,1,3,4 should be present
        assert sorted(all_wids) == [0, 1, 3, 4]


# ---------------------------------------------------------------------------
# KeepRange
# ---------------------------------------------------------------------------

class TestKeepRange:
    def test_keep_range_excludes_rest(self):
        t = _simple_transcript(8)
        log = _push_ops([KeepRange(word_ids=[3, 4, 5])])
        tl = apply(log, t)
        all_wids = [w for seg in tl.segments for w in seg.source_word_ids]
        assert all_wids == [3, 4, 5]


# ---------------------------------------------------------------------------
# ReorderSections
# ---------------------------------------------------------------------------

class TestReorderSections:
    def test_reorder_reverses_halves(self):
        t = _simple_transcript(6)
        log = _push_ops([ReorderSections(sections=[[3, 4, 5], [0, 1, 2]])])
        tl = apply(log, t)
        all_wids = [w for seg in tl.segments for w in seg.source_word_ids]
        assert all_wids == [3, 4, 5, 0, 1, 2]

    def test_reorder_splits_on_non_adjacency(self):
        t = _simple_transcript(6)
        log = _push_ops([ReorderSections(sections=[[3, 4, 5], [0, 1, 2]])])
        tl = apply(log, t)
        # Two sections → two segments
        assert len(tl.segments) == 2

    def test_reorder_timing_starts_at_zero(self):
        t = _simple_transcript(6)
        log = _push_ops([ReorderSections(sections=[[4, 5], [0, 1, 2, 3]])])
        tl = apply(log, t)
        assert tl.segments[0].start_time == pytest.approx(0.0)
        assert tl.segments[1].start_time == pytest.approx(tl.segments[0].end_time)


# ---------------------------------------------------------------------------
# SpeedRange
# ---------------------------------------------------------------------------

class TestSpeedRange:
    def test_speed_2x_halves_duration(self):
        t = _simple_transcript(4)
        log = _push_ops([SpeedRange(word_ids=[0, 1, 2, 3], factor=2.0)])
        tl = apply(log, t)
        original_tl = apply(OperationLog(), t)
        assert tl.total_duration == pytest.approx(
            original_tl.total_duration / 2.0, rel=0.01
        )

    def test_speed_half_doubles_duration(self):
        t = _simple_transcript(4)
        log = _push_ops([SpeedRange(word_ids=[0, 1, 2, 3], factor=0.5)])
        tl = apply(log, t)
        original_tl = apply(OperationLog(), t)
        assert tl.total_duration == pytest.approx(
            original_tl.total_duration * 2.0, rel=0.01
        )

    def test_speed_last_write_wins(self):
        """Overlapping SpeedRange ops: the LAST one wins for shared words."""
        t = _simple_transcript(4)
        log = _push_ops([
            SpeedRange(word_ids=[0, 1, 2, 3], factor=2.0),
            SpeedRange(word_ids=[2, 3], factor=4.0),  # overrides for words 2,3
        ])
        tl = apply(log, t)
        # Words 0,1 → 2x speed segment; words 2,3 → 4x speed segment
        assert len(tl.segments) == 2
        assert tl.segments[0].speed_factor == pytest.approx(2.0)
        assert tl.segments[1].speed_factor == pytest.approx(4.0)

    def test_speed_splits_segment(self):
        t = _simple_transcript(6)
        # Apply different speeds to two halves
        log = _push_ops([
            SpeedRange(word_ids=[0, 1, 2], factor=1.5),
            SpeedRange(word_ids=[3, 4, 5], factor=2.0),
        ])
        tl = apply(log, t)
        assert len(tl.segments) == 2


# ---------------------------------------------------------------------------
# MuteWords / BleepWords
# ---------------------------------------------------------------------------

class TestMuteAndBleep:
    def test_mute_flag_propagates(self):
        t = _simple_transcript(4)
        log = _push_ops([MuteWords(word_ids=[1, 2])])
        tl = apply(log, t)
        muted_segs = [s for s in tl.segments if s.is_muted]
        assert any(1 in s.source_word_ids for s in muted_segs)

    def test_bleep_flag_propagates(self):
        t = _simple_transcript(4)
        log = _push_ops([BleepWords(word_ids=[0])])
        tl = apply(log, t)
        bleeped_segs = [s for s in tl.segments if s.is_bleeped]
        assert any(0 in s.source_word_ids for s in bleeped_segs)

    def test_mute_splits_segment(self):
        t = _simple_transcript(4)
        log = _push_ops([MuteWords(word_ids=[2])])
        tl = apply(log, t)
        assert len(tl.segments) >= 2


# ---------------------------------------------------------------------------
# RemoveFillers
# ---------------------------------------------------------------------------

class TestRemoveFillers:
    def test_removes_fillers_from_transcript(self):
        t = make_single_speaker()
        log = _push_ops([RemoveFillers(filler_words=["um", "uh", "like"])])
        tl = apply(log, t)
        all_wids = {w for seg in tl.segments for w in seg.source_word_ids}
        for fid in FILLER_IDS:
            assert fid not in all_wids

    def test_remove_fillers_shortens_duration(self):
        t = make_single_speaker()
        original = apply(OperationLog(), t)
        log = _push_ops([RemoveFillers(filler_words=["um", "uh", "like"])])
        tl = apply(log, t)
        assert tl.total_duration < original.total_duration

    def test_filler_count_matches_fixture(self):
        """Fixture guarantees at least 4 um+uh+like fillers."""
        assert len(FILLER_IDS) >= 4

    def test_case_insensitive_filler(self):
        """Filler list should match regardless of case in transcript."""
        words = [
            Word(id=0, text="Hello", start=0.0, end=0.4),
            Word(id=1, text="Um,", start=0.5, end=0.7),   # capital, comma
            Word(id=2, text="world", start=0.8, end=1.1),
        ]
        t = Transcript(words=words)
        log = _push_ops([RemoveFillers(filler_words=["um"])])
        tl = apply(log, t)
        all_wids = {w for seg in tl.segments for w in seg.source_word_ids}
        assert 1 not in all_wids


# ---------------------------------------------------------------------------
# RemoveSilences
# ---------------------------------------------------------------------------

class TestRemoveSilences:
    def _transcript_with_silence(self) -> Transcript:
        """4 words with a 1.5 s gap between words 1 and 2."""
        words = [
            Word(id=0, text="a", start=0.0, end=0.3),
            Word(id=1, text="b", start=0.4, end=0.7),
            # 1.5 s silence here
            Word(id=2, text="c", start=2.2, end=2.5),
            Word(id=3, text="d", start=2.6, end=2.9),
        ]
        return Transcript(words=words)

    def test_no_silence_removal_keeps_gap(self):
        t = self._transcript_with_silence()
        tl = apply(OperationLog(), t)
        # One segment; duration = source end - source start = 2.9
        assert tl.total_duration == pytest.approx(2.9, abs=0.01)

    def test_remove_silences_compresses_gap(self):
        t = self._transcript_with_silence()
        log = _push_ops([RemoveSilences(min_silence_secs=0.5, max_gap_secs=0.1)])
        tl = apply(log, t)
        # Gap of 1.5 s > 0.5 s threshold → compressed to 0.1 s
        # Removed: 1.5 - 0.1 = 1.4 s
        assert tl.total_duration == pytest.approx(2.9 - 1.4, abs=0.02)

    def test_gap_below_threshold_unchanged(self):
        t = self._transcript_with_silence()
        # Threshold is 2.0 s, gap is only 1.5 s → no compression
        log = _push_ops([RemoveSilences(min_silence_secs=2.0, max_gap_secs=0.1)])
        tl = apply(log, t)
        assert tl.total_duration == pytest.approx(2.9, abs=0.01)


# ---------------------------------------------------------------------------
# Caption: follows words after structural edits
# ---------------------------------------------------------------------------

class TestCaptionFollowsWords:
    def _transcript_with_caption(self):
        """5 words; caption anchored to words 2-3."""
        words = [
            Word(id=i, text=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4)
            for i in range(5)
        ]
        return Transcript(words=words)

    def _log_with_caption(self, word_ids: list[int]) -> OperationLog:
        return _push_ops([AddCaption(word_ids=word_ids, text="CAP")])

    # After a cut that does NOT touch the caption words
    def test_caption_survives_unrelated_cut(self):
        t = self._transcript_with_caption()
        log = _push_ops([
            AddCaption(word_ids=[2, 3], text="CAP"),
            CutRange(word_ids=[0, 1]),  # cut words 0,1 – not caption anchors
        ])
        tl = apply(log, t)
        assert len(tl.captions) == 1
        cap = tl.captions[0]
        # Caption should start BEFORE where word 2 used to start (timing shifted left)
        # Words 0,1 are gone → word 2 now starts at t=0
        assert cap.start_time == pytest.approx(0.0, abs=0.01)

    # After a speed change on the caption section
    def test_caption_timing_shifts_with_speed(self):
        t = self._transcript_with_caption()
        log = _push_ops([
            AddCaption(word_ids=[2, 3], text="CAP"),
            SpeedRange(word_ids=[2, 3], factor=2.0),
        ])
        tl_normal = apply(_push_ops([AddCaption(word_ids=[2, 3], text="CAP")]), t)
        tl_fast = apply(log, t)
        cap_normal = tl_normal.captions[0]
        cap_fast = tl_fast.captions[0]
        # 2x speed → caption duration should be ~half
        assert cap_fast.duration < cap_normal.duration

    # After a reorder
    def test_caption_follows_words_after_reorder(self):
        t = self._transcript_with_caption()
        log = _push_ops([
            AddCaption(word_ids=[0, 1], text="CAP"),
            ReorderSections(sections=[[2, 3, 4], [0, 1]]),  # move 0,1 to end
        ])
        tl = apply(log, t)
        assert len(tl.captions) == 1
        cap = tl.captions[0]
        # Words 0,1 are now at the end → caption should start after words 2-4
        seg_for_234 = next(s for s in tl.segments if 2 in s.source_word_ids)
        assert cap.start_time >= seg_for_234.start_time

    # Full cut → caption removed
    def test_caption_fully_cut_is_removed_with_warning(self):
        t = self._transcript_with_caption()
        log = _push_ops([
            AddCaption(word_ids=[2, 3], text="CAP"),
            CutRange(word_ids=[2, 3]),
        ])
        tl = apply(log, t)
        assert len(tl.captions) == 0
        assert len(tl.removed_captions) == 1
        assert any("cut" in w.lower() for w in tl.warnings)

    # Partial cut → caption survives with surviving words
    def test_caption_partially_cut_survives(self):
        t = self._transcript_with_caption()
        log = _push_ops([
            AddCaption(word_ids=[2, 3], text="CAP"),
            CutRange(word_ids=[3]),  # cut only word 3
        ])
        tl = apply(log, t)
        assert len(tl.captions) == 1
        assert 2 in tl.captions[0].word_ids   # word 2 survives
        assert 3 not in tl.captions[0].word_ids
        # Warning should mention partial cut
        assert any("partial" in w.lower() for w in tl.warnings)

    # Minimum display time
    def test_caption_min_display_enforced(self):
        """Very short word → caption extended to 0.7 s minimum."""
        words = [
            Word(id=0, text="a", start=0.0, end=0.05),   # only 50ms
            Word(id=1, text="b", start=0.1, end=0.15),
        ]
        t = Transcript(words=words)
        log = _push_ops([AddCaption(word_ids=[0], text="Short")])
        tl = apply(log, t)
        assert len(tl.captions) == 1
        assert tl.captions[0].duration >= 0.7

    # Cut-then-restore: caption returns
    def test_cut_captioned_sentence_then_restore_returns_caption(self):
        t = self._transcript_with_caption()
        log = _push_ops([AddCaption(word_ids=[2, 3], text="CAP")])
        cut_op = CutRange(word_ids=[2, 3])
        log = log.push(cut_op)
        cut_op_id = log.ops[-1].op_id

        # Verify caption is gone
        tl_cut = apply(log, t)
        assert len(tl_cut.captions) == 0

        # Restore the cut op (deactivate it)
        log_restored = log.restore_op(cut_op_id)
        tl_restored = apply(log_restored, t)
        assert len(tl_restored.captions) == 1


# ---------------------------------------------------------------------------
# AutoCaption
# ---------------------------------------------------------------------------

class TestAutoCaption:
    def test_auto_caption_derives_text(self):
        words = [
            Word(id=0, text="Hello", start=0.0, end=0.4),
            Word(id=1, text="world", start=0.5, end=0.9),
        ]
        t = Transcript(words=words)
        log = _push_ops([AutoCaption(word_ids=[0, 1])])
        tl = apply(log, t)
        assert len(tl.captions) == 1
        assert tl.captions[0].text == "Hello world"


# ---------------------------------------------------------------------------
# AddMusicDucked (data-only)
# ---------------------------------------------------------------------------

class TestAddMusicDucked:
    def test_music_op_does_not_affect_segments(self):
        t = _simple_transcript(4)
        log_no_music = OperationLog()
        log_with_music = _push_ops([
            AddMusicDucked(word_ids=[0, 1], track_ref="bg.mp3")
        ])
        tl_base = apply(log_no_music, t)
        tl_music = apply(log_with_music, t)
        assert len(tl_music.segments) == len(tl_base.segments)
        assert tl_music.total_duration == pytest.approx(tl_base.total_duration)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_log_same_hash_three_times(self):
        t = make_single_speaker()
        log = _push_ops([
            RemoveFillers(filler_words=["um", "uh", "like"]),
            AddCaption(word_ids=PRICING_IDS[:1], text="Pricing"),
            CutRange(word_ids=THANKS_IDS[:1]),
        ])
        hashes = [apply(log, t).timeline_hash for _ in range(3)]
        assert hashes[0] == hashes[1] == hashes[2]

    def test_empty_log_hash_is_stable(self):
        t = _simple_transcript(5)
        h1 = apply(OperationLog(), t).timeline_hash
        h2 = apply(OperationLog(), t).timeline_hash
        assert h1 == h2

    def test_different_logs_different_hashes(self):
        t = _simple_transcript(5)
        h1 = apply(_push_ops([CutRange(word_ids=[0])]), t).timeline_hash
        h2 = apply(_push_ops([CutRange(word_ids=[1])]), t).timeline_hash
        assert h1 != h2
