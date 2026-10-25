"""
tests/test_captions_v2.py

Tests for the pure caption chunker function and sentence_end alignment:
- Break at sentence_end flag
- Break at gaps >= 0.3s
- Max 5 words / 30 characters limit
- "welcome to nexus | nexus is the link" with 0.3s gap split
- "very very good" NOT split (verifies no repeated consecutive words rule)
- Vosk scenario: zero sentence_end flags cleanly chunked by gap and length
- Captions surviving cut/speed/restore
"""

from __future__ import annotations

import pytest

from promptcut.engine import apply
from promptcut.models.operation_log import OperationLog
from promptcut.models.operations import AddCaption, CutRange, SpeedRange
from promptcut.models.transcript import Transcript, Word
from promptcut.render.caption_chunker import chunk_transcript_for_captions


class TestCaptionChunkerPureFunction:
    """Verifies pure caption chunker rules."""

    def test_sentence_end_break(self):
        """Sentence end flag forces a clean break."""
        words = [
            Word(id=0, text="Hello", start=0.0, end=0.3, sentence_end=True),
            Word(id=1, text="world", start=0.35, end=0.6, sentence_end=False),
        ]
        tr = Transcript(words=words, source_duration=1.0)
        chunks = chunk_transcript_for_captions(tr)
        assert len(chunks) == 2
        assert [w.text for w in chunks[0]] == ["Hello"]
        assert [w.text for w in chunks[1]] == ["world"]

    def test_gap_threshold_break_nexus_example(self):
        """'welcome to nexus | nexus is the link' with 0.3s gap splits cleanly."""
        words = [
            Word(id=0, text="welcome", start=0.0, end=0.4),
            Word(id=1, text="to", start=0.4, end=0.7),
            Word(id=2, text="nexus", start=0.7, end=1.2),
            # Gap between word 2 (end=1.2) and word 3 (start=1.5) is exactly 0.3s
            Word(id=3, text="nexus", start=1.5, end=1.9),
            Word(id=4, text="is", start=1.9, end=2.1),
            Word(id=5, text="the", start=2.1, end=2.3),
            Word(id=6, text="link", start=2.3, end=2.7),
        ]
        tr = Transcript(words=words, source_duration=3.0)
        chunks = chunk_transcript_for_captions(tr)
        assert len(chunks) == 2
        assert [w.text for w in chunks[0]] == ["welcome", "to", "nexus"]
        assert [w.text for w in chunks[1]] == ["nexus", "is", "the", "link"]

    def test_repeated_consecutive_words_not_split(self):
        """'very very good' with small gap (<0.3s) is NOT split (no repeated words rule)."""
        words = [
            Word(id=0, text="very", start=0.0, end=0.3),
            Word(id=1, text="very", start=0.35, end=0.65),  # gap = 0.05s
            Word(id=2, text="good", start=0.7, end=1.0),    # gap = 0.05s
        ]
        tr = Transcript(words=words, source_duration=1.5)
        chunks = chunk_transcript_for_captions(tr)
        assert len(chunks) == 1
        assert [w.text for w in chunks[0]] == ["very", "very", "good"]

    def test_zero_sentence_end_flags_vosk_scenario(self):
        """When transcript has zero sentence_end flags (Vosk), chunking relies on gaps and length."""
        words = [
            Word(id=0, text="one", start=0.0, end=0.2, sentence_end=False),
            Word(id=1, text="two", start=0.22, end=0.4, sentence_end=False),
            Word(id=2, text="three", start=0.42, end=0.6, sentence_end=False),
            Word(id=3, text="four", start=0.62, end=0.8, sentence_end=False),
            Word(id=4, text="five", start=0.82, end=1.0, sentence_end=False),
            # Exceeds max 5 words limit -> word 5 forces a new chunk
            Word(id=5, text="six", start=1.02, end=1.2, sentence_end=False),
        ]
        tr = Transcript(words=words, source_duration=1.5)
        chunks = chunk_transcript_for_captions(tr)
        assert len(chunks) == 2
        assert [w.text for w in chunks[0]] == ["one", "two", "three", "four", "five"]
        assert [w.text for w in chunks[1]] == ["six"]

    def test_max_30_chars_limit(self):
        """Chunks respect max 30 character limit for space-joined text."""
        words = [
            Word(id=0, text="supercalifragilistic", start=0.0, end=0.5), # 20 chars
            Word(id=1, text="expialidocious", start=0.55, end=1.0),      # 14 chars -> 20 + 1 + 14 = 35 > 30 chars
        ]
        tr = Transcript(words=words, source_duration=1.5)
        chunks = chunk_transcript_for_captions(tr)
        assert len(chunks) == 2
        assert [w.text for w in chunks[0]] == ["supercalifragilistic"]
        assert [w.text for w in chunks[1]] == ["expialidocious"]

    def test_captions_survive_cut_speed_restore(self):
        """Generated captions survive cuts, speed changes, and undo/restore."""
        words = [
            Word(id=0, text="welcome", start=0.0, end=0.4),
            Word(id=1, text="to", start=0.4, end=0.7),
            Word(id=2, text="nexus", start=0.7, end=1.2),
            Word(id=3, text="nexus", start=1.5, end=1.9),
            Word(id=4, text="is", start=1.9, end=2.1),
            Word(id=5, text="the", start=2.1, end=2.3),
            Word(id=6, text="link", start=2.3, end=2.7),
        ]
        tr = Transcript(words=words, source_duration=3.0)
        chunks = chunk_transcript_for_captions(tr)

        # Build initial log with the two captions
        log = OperationLog()
        for ch in chunks:
            log = log.push(AddCaption(word_ids=[w.id for w in ch], text=" ".join(w.text for w in ch)))

        tl1 = apply(log, tr)
        assert len(tl1.captions) == 2
        assert tl1.captions[0].text == "welcome to nexus"
        assert tl1.captions[1].text == "nexus is the link"

        # Cut word 0 ("welcome") -> partial cut on caption 0, caption survives
        log = log.push(CutRange(word_ids=[0]))
        tl2 = apply(log, tr)
        assert len(tl2.captions) == 2
        assert tl2.captions[0].word_ids == [1, 2]

        # Speed up remaining caption 0 words by 2x
        log = log.push(SpeedRange(word_ids=[1, 2], factor=2.0))
        tl3 = apply(log, tr)
        assert len(tl3.captions) == 2
        assert tl3.captions[0].duration < tl1.captions[0].duration

        # Undo speedup
        log = log.undo()
        tl4 = apply(log, tr)
        assert len(tl4.captions) == 2

        # Restore op 2 (the cut)
        log = log.restore_op(2)
        tl5 = apply(log, tr)
        assert len(tl5.captions) == 2
        assert tl5.captions[0].word_ids == [0, 1, 2]
