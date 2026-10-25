"""
tests/test_resolver.py

Tests for phrase_search, parse_time_expression, time_range_to_word_ids,
and the MeaningSearchAdapter mock.
"""

import pytest

from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.transcript import Transcript, Word
from promptcut.resolver import (
    MockMeaningSearchAdapter,
    parse_time_expression,
    phrase_search,
    time_range_to_word_ids,
)
from tests.fixtures.single_speaker import make_single_speaker, PRICING_IDS, THANKS_IDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transcript(*words: str) -> Transcript:
    ws = [
        Word(id=i, text=w, start=i * 0.5, end=i * 0.5 + 0.4)
        for i, w in enumerate(words)
    ]
    return Transcript(words=ws)


# ---------------------------------------------------------------------------
# phrase_search
# ---------------------------------------------------------------------------

class TestPhraseSearch:
    def test_single_word_match(self):
        t = _transcript("hello", "world", "hello")
        hits = phrase_search(t, "hello")
        assert len(hits) == 2
        assert [0] in hits
        assert [2] in hits

    def test_multi_word_phrase(self):
        t = _transcript("remove", "the", "pricing", "section", "pricing")
        hits = phrase_search(t, "the pricing")
        assert len(hits) == 1
        assert hits[0] == [1, 2]

    def test_no_match_returns_empty(self):
        t = _transcript("alpha", "beta")
        assert phrase_search(t, "gamma") == []

    def test_punctuation_stripped_in_match(self):
        t = _transcript("Hey,", "world!")
        hits = phrase_search(t, "hey")
        assert len(hits) == 1

    def test_pricing_three_times_in_fixture(self):
        t = make_single_speaker()
        hits = phrase_search(t, "pricing")
        assert len(hits) == 3
        # IDs should match the exported constant
        found_ids = [h[0] for h in hits]
        assert set(found_ids) == set(PRICING_IDS)

    def test_thanks_four_times_in_fixture(self):
        t = make_single_speaker()
        hits = phrase_search(t, "thanks")
        assert len(hits) == 4
        found_ids = [h[0] for h in hits]
        assert set(found_ids) == set(THANKS_IDS)

    def test_case_insensitive(self):
        t = _transcript("Hello", "world")
        assert phrase_search(t, "hello") == [[0]]
        assert phrase_search(t, "WORLD") == [[1]]

    def test_empty_phrase_returns_empty(self):
        t = _transcript("a", "b")
        assert phrase_search(t, "") == []

    def test_phrase_longer_than_transcript(self):
        t = _transcript("a", "b")
        assert phrase_search(t, "a b c") == []


# ---------------------------------------------------------------------------
# parse_time_expression
# ---------------------------------------------------------------------------

class TestParseTimeExpression:
    def test_seconds_only(self):
        assert parse_time_expression("80 seconds") == pytest.approx(80.0)
        assert parse_time_expression("80s") == pytest.approx(80.0)

    def test_colon_mm_ss(self):
        assert parse_time_expression("1:20") == pytest.approx(80.0)
        assert parse_time_expression("01:20") == pytest.approx(80.0)

    def test_colon_hh_mm_ss(self):
        assert parse_time_expression("1:20:05") == pytest.approx(4805.0)

    def test_plain_number(self):
        assert parse_time_expression("90") == pytest.approx(90.0)
        assert parse_time_expression("1.5") == pytest.approx(1.5)

    def test_word_seconds(self):
        assert parse_time_expression("30 sec") == pytest.approx(30.0)
        assert parse_time_expression("30 secs") == pytest.approx(30.0)

    def test_one_minute(self):
        assert parse_time_expression("1 minute") == pytest.approx(60.0)
        assert parse_time_expression("a minute") == pytest.approx(60.0)

    def test_minutes_and_seconds(self):
        assert parse_time_expression("1 minute 20 seconds") == pytest.approx(80.0)
        assert parse_time_expression("1 min 20 sec") == pytest.approx(80.0)

    def test_spoken_a_minute_twenty(self):
        assert parse_time_expression("a minute twenty") == pytest.approx(80.0)

    def test_two_minutes(self):
        assert parse_time_expression("2 minutes") == pytest.approx(120.0)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_time_expression("the quick brown fox")


# ---------------------------------------------------------------------------
# time_range_to_word_ids
# ---------------------------------------------------------------------------

class TestTimeRangeToWordIds:
    def test_returns_words_in_range(self):
        # Words at 0-0.4, 0.5-0.9, 1.0-1.4, 1.5-1.9
        ws = [Word(id=i, text=f"w{i}", start=i*0.5, end=i*0.5+0.4) for i in range(4)]
        t = Transcript(words=ws)
        tl = apply(OperationLog(), t)
        ids = time_range_to_word_ids(t, tl, 0.5, 1.5)
        assert 1 in ids
        assert 2 in ids
        assert 0 not in ids   # ends at 0.4, range starts at 0.5

    def test_returns_empty_when_no_overlap(self):
        ws = [Word(id=i, text=f"w{i}", start=i*0.5, end=i*0.5+0.4) for i in range(3)]
        t = Transcript(words=ws)
        tl = apply(OperationLog(), t)
        ids = time_range_to_word_ids(t, tl, 10.0, 20.0)
        assert ids == []

    def test_out_of_range_time_rejected_by_caller(self):
        """
        The resolver itself just returns [] for out-of-range times.
        The response layer is responsible for issuing Rejected.
        """
        ws = [Word(id=i, text=f"w{i}", start=i*0.5, end=i*0.5+0.4) for i in range(3)]
        t = Transcript(words=ws)
        tl = apply(OperationLog(), t)
        assert time_range_to_word_ids(t, tl, 99.0, 100.0) == []

    def test_uses_output_timeline_coords_after_cut(self):
        """After a cut, output times shift left; resolver should use output times."""
        ws = [Word(id=i, text=f"w{i}", start=i*0.5, end=i*0.5+0.4) for i in range(5)]
        t = Transcript(words=ws)
        from promptcut.models.operations import CutRange
        from promptcut.models.operation_log import OperationLog as OL
        log = OL().push(CutRange(word_ids=[0, 1]))  # cut first two words
        tl = apply(log, t)
        # Words 2,3,4 now start at output time 0
        ids = time_range_to_word_ids(t, tl, 0.0, 0.6)
        # Word 2 should be in range
        assert 2 in ids


# ---------------------------------------------------------------------------
# MeaningSearchAdapter mock
# ---------------------------------------------------------------------------

class TestMockMeaningSearch:
    def test_mock_returns_empty(self):
        adapter = MockMeaningSearchAdapter()
        t = make_single_speaker()
        result = adapter.search(t, "something about pricing")
        assert result == []
