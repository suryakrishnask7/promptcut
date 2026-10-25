"""
Two-speaker fixture transcript (~90 seconds).

Speaker S1 asks questions; speaker S2 answers.
Includes "thanks" ×2, fillers, one silence gap.
"""

from __future__ import annotations

from promptcut.models.transcript import Transcript, Word
from tests.fixtures.single_speaker import _build_words

_SEGMENTS_2SPK: list[tuple[float, list[str], str]] = [
    # (start, [words], speaker)
    (0.00,  ["Hello,", "and", "welcome", "to", "the", "interview."], "S1"),
    (3.20,  ["Um,", "let's", "dive", "right", "in."], "S1"),
    (5.80,  ["Sure,", "thanks", "for", "having", "me."], "S2"),   # thanks #1
    (8.60,  ["So,", "tell", "me", "about", "your", "background."], "S1"),
    (11.60, ["Of", "course."], "S2"),
    (13.00, ["I've", "been", "working", "in", "software", "for", "ten", "years."], "S2"),
    (17.50, ["Mostly", "backend", "systems,", "uh,", "distributed", "architecture."], "S2"),
    (21.80, ["That's", "great.", "And", "what", "drew", "you", "here?"], "S1"),
    # silence gap (~1.0 s)
    (26.50, ["Honestly,", "like,", "the", "mission", "resonated", "with", "me."], "S2"),
    (30.40, ["I", "think", "there's", "a", "real", "problem", "to", "solve."], "S2"),
    (34.50, ["I", "appreciate", "that.", "Um,", "one", "more", "question."], "S1"),
    (38.80, ["What", "do", "you", "consider", "your", "biggest", "strength?"], "S1"),
    (43.00, ["Uh,", "definitely", "debugging", "complex", "systems."], "S2"),
    (47.20, ["I", "stay", "calm", "under", "pressure."], "S2"),
    (50.40, ["Perfect.", "Thanks", "for", "your", "time", "today."], "S1"),   # thanks #2
    (54.00, ["Absolutely.", "It", "was", "a", "pleasure."], "S2"),
    (57.20, ["We'll", "be", "in", "touch."], "S1"),
    (59.40, ["Looking", "forward", "to", "it."], "S2"),
]


def make_two_speaker() -> Transcript:
    """Build and return the two-speaker interview fixture transcript."""
    words: list[Word] = []
    wid = 0
    for seg_start, texts, spk in _SEGMENTS_2SPK:
        seg_words = _build_words([(seg_start, texts)], speaker=spk, start_id=wid)
        words.extend(seg_words)
        wid += len(seg_words)
    return Transcript(words=words, title="Interview – two speakers")
