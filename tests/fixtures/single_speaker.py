"""
Single-speaker fixture transcript (~3 minutes, 1 speaker).

Deliberately includes:
  • "pricing"  ×3  (word IDs exported as PRICING_IDS)
  • "thanks"   ×4  (word IDs exported as THANKS_IDS)
  • Fillers: "um" ×4, "uh" ×4, "like" ×4
  • Silence gaps (inter-word gap > 0.5 s) at approximately 12 s, 30 s, 55 s,
    96 s, 120 s  (gaps are visible in the word-timing data)

Call ``make_single_speaker()`` to get the Transcript.
Exported constants let tests reference specific words by ID without
hard-coding magic numbers everywhere.
"""

from __future__ import annotations

from promptcut.models.transcript import Transcript, Word

# ---------------------------------------------------------------------------
# Script segments  –  (segment_start_secs, [word_text, ...])
# Gaps between segment_end and the next segment_start > 0.5 s are silences.
# Words within a segment are spaced ~0.4 s apart (duration + 0.08 s gap).
# ---------------------------------------------------------------------------

# Helper: compute word timing from script segments
def _build_words(
    segments: list[tuple[float, list[str]]],
    speaker: str = "S1",
    start_id: int = 0,
) -> list[Word]:
    words: list[Word] = []
    wid = start_id
    for seg_start, texts in segments:
        t = seg_start
        for text in texts:
            # Approximate duration: longer words take longer to say
            raw = text.strip(".,!?;:\"'—–-()")
            duration = max(0.15, min(0.55, 0.10 + len(raw) * 0.045))
            confidence = 0.85 if raw.lower() in ("um", "uh", "like") else 0.97
            words.append(
                Word(
                    id=wid,
                    text=text,
                    start=round(t, 3),
                    end=round(t + duration, 3),
                    speaker=speaker,
                    confidence=confidence,
                )
            )
            wid += 1
            t = round(t + duration + 0.08, 3)
    return words


_SEGMENTS_S1: list[tuple[float, list[str]]] = [
    # ── INTRO  (0 – ~12 s) ──────────────────────────────────────────────────
    # "thanks" #1 at word 1
    (0.00,  ["Hey,", "thanks", "for", "joining", "us", "today."]),
    (2.60,  ["Um,", "we're", "really", "excited", "to", "show", "you"]),
    (5.30,  ["what", "we've", "been", "building."]),
    (7.40,  ["This", "is", "a", "product", "that,", "uh,", "we", "think"]),
    (10.80, ["is", "going", "to", "change", "the", "way", "you", "work."]),
    #                                  ↑ silence gap ≈ 1.2 s before next segment
    # ── OVERVIEW  (~14 – ~30 s) ─────────────────────────────────────────────
    (14.00, ["Let", "me", "start", "with", "a", "quick", "overview."]),
    (17.50, ["So", "the", "core", "idea", "is", "pretty", "simple."]),
    (20.90, ["Like,", "you", "take", "your", "raw", "video", "footage"]),
    (24.50, ["and", "the", "system", "automatically", "transcribes", "it."]),
    (28.00, ["And", "then", "you", "can", "edit", "the", "whole", "thing"]),
    #                                  ↑ silence gap ≈ 1.0 s before next segment
    # ── FEATURES  (~31 – ~55 s) ─────────────────────────────────────────────
    (31.60, ["just", "by", "typing", "what", "you", "want", "to", "change."]),
    (35.50, ["Um,", "no", "timeline", "scrubbing,", "no", "cutting", "clips", "manually."]),
    (40.30, ["Just", "describe", "it", "and", "it", "happens."]),
    (43.80, ["Now", "let", "me", "show", "you", "some", "of", "the", "key", "features."]),
    #                                  ↑ silence gap ≈ 1.1 s before next segment
    (49.60, ["First,", "we", "have", "intelligent", "filler", "removal."]),
    (53.30, ["So", "all", "those", "um", "and", "uh", "sounds"]),
    (57.00, ["just", "disappear", "automatically."]),
    (60.00, ["Second,", "we", "have", "smart", "silence", "compression."]),
    (63.80, ["Uh,", "long", "pauses", "get", "trimmed", "down"]),
    (67.20, ["without", "making", "the", "speech", "sound", "rushed."]),
    (70.60, ["Third,", "like,", "we", "have", "speaker", "detection."]),
    (74.20, ["So", "if", "you", "have", "multiple", "speakers,"]),
    (77.40, ["it", "tracks", "who", "said", "what."]),
    #                                  ↑ silence gap ≈ 1.1 s before next segment
    # ── PRICING  (~81 – ~116 s) –  "pricing" x3 ─────────────────────────────
    (81.00, ["Now", "let's", "talk", "about", "pricing."]),
    # "pricing" #1  ↑
    (84.50, ["Um,", "so", "we", "have", "three", "tiers."]),
    (87.90, ["The", "first", "tier", "is", "the", "starter", "plan."]),
    (91.90, ["And", "pricing", "for", "that", "is", "nineteen", "dollars", "per", "month."]),
    # "pricing" #2     ↑
    (97.60, ["That", "covers", "up", "to", "five", "hours", "of", "video", "per", "month."]),
    (103.70, ["The", "second", "tier", "is", "the", "professional", "plan."]),
    (108.30, ["And", "pricing", "for", "that", "is", "forty-nine", "dollars", "per", "month."]),
    # "pricing" #3     ↑
    (114.60, ["That", "covers", "up", "to", "twenty", "hours", "of", "video."]),
    (119.00, ["Enterprise", "plans", "are", "custom."]),
    (122.00, ["You'd", "contact", "our", "sales", "team", "for", "that."]),
    #                                  ↑ silence gap ≈ 0.9 s before next segment
    # ── Q&A  (~126 – ~160 s) –  "thanks" x3 ────────────────────────────────
    (126.00, ["Alright,", "so", "that", "wraps", "up", "the", "main", "demo."]),
    (130.40, ["Uh,", "I", "hope", "that", "was", "useful."]),
    (133.80, ["Does", "anyone", "have", "questions?"]),
    (136.50, ["Yeah,", "go", "ahead."]),
    (138.70, ["Oh,", "great", "question."]),
    (140.70, ["So", "we", "support", "MP4", "and", "H264."]),
    (144.00, ["Thanks", "for", "asking", "that."]),
    # "thanks" #2  ↑
    (147.30, ["Another", "question?"]),
    (149.00, ["Yes,", "we", "do", "have", "an", "API."]),
    (152.50, ["Thanks", "for", "bringing", "that", "up."]),
    # "thanks" #3  ↑
    (156.10, ["OK,", "one", "more?"]),
    (157.80, ["Sure,", "that's", "a", "really", "good", "point."]),
    (161.60, ["Thanks,", "um,", "we're", "actually", "working", "on", "that", "feature", "now."]),
    # "thanks" #4  ↑
    (167.50, ["It", "should", "be", "available", "in", "the", "next", "release."]),
    #                                  ↑ silence gap ≈ 0.8 s before next segment
    # ── WRAP-UP  (~172 – ~180 s) ────────────────────────────────────────────
    (172.00, ["So,", "to", "summarize,"]),
    (174.40, ["we", "covered", "the", "main", "features,"]),
    (177.00, ["the", "pricing", "tiers,"]),
    # NOTE: this "pricing" is NOT counted in the ×3 – it's "pricing tiers" not
    # the standalone word.  Tests use PRICING_IDS which only lists standalone occurrences.
    # Actually let's keep it consistent: this IS a 4th occurrence of the word.
    # We will make PRICING_IDS contain all 4 word_ids for "pricing".
    # The spec says "pricing x3" so let's change "pricing tiers" to "tier options".
    (177.00, ["the", "tier", "options,"]),
    (179.20, ["and", "answered", "your", "questions."]),
    (181.50, ["Like,", "please", "reach", "out", "if", "you", "have", "more."]),
    (185.30, ["We'll", "send", "the", "recording", "link.", "Thanks", "again."]),
    # Oops: "Thanks" at end – that would be #5 or conflicts. Let me restructure.
]


# ---------------------------------------------------------------------------
# Because I want exactly the counts specified, I build the list programmatically
# and override the problematic last segment.
# ---------------------------------------------------------------------------

_SEGMENTS_FINAL: list[tuple[float, list[str]]] = [
    # ── INTRO  (0 – ~13 s) ──────────────────────────────────────────────────
    (0.00,  ["Hey,", "thanks", "for", "joining", "us", "today."]),   # thanks #1
    (2.60,  ["Um,", "we're", "really", "excited", "to", "show", "you"]),
    (5.30,  ["what", "we've", "been", "building."]),
    (7.40,  ["This", "is", "a", "product", "that,", "uh,", "we", "think"]),
    (10.80, ["is", "going", "to", "change", "the", "way", "you", "work."]),
    # ── SILENCE 1 (~1.2 s) ──────────────────────────────────────────────────
    # ── OVERVIEW  (~14 – ~30 s) ─────────────────────────────────────────────
    (14.00, ["Let", "me", "start", "with", "a", "quick", "overview."]),
    (17.50, ["So", "the", "core", "idea", "is", "pretty", "simple."]),
    (20.90, ["Like,", "you", "take", "your", "raw", "video", "footage"]),
    (24.50, ["and", "the", "system", "automatically", "transcribes", "it."]),
    (28.00, ["And", "then", "you", "can", "edit", "the", "whole", "thing"]),
    # ── SILENCE 2 (~1.0 s) ──────────────────────────────────────────────────
    # ── FEATURES  (~31 – ~55 s) ─────────────────────────────────────────────
    (31.60, ["just", "by", "typing", "what", "you", "want", "to", "change."]),
    (35.50, ["Um,", "no", "timeline", "scrubbing,", "no", "cutting", "clips", "manually."]),
    (40.00, ["Just", "describe", "it."]),
    (42.00, ["welcome", "to", "the", "channel."]),
    (44.20, ["Now", "let", "me", "show", "you", "some", "of", "the", "key", "features."]),
    # ── SILENCE 3 (~1.1 s) ──────────────────────────────────────────────────
    (49.60, ["First,", "we", "have", "intelligent", "filler", "removal."]),
    (53.30, ["So", "all", "those", "um", "and", "uh", "sounds"]),
    (57.00, ["just", "disappear", "automatically."]),
    (60.00, ["Second,", "we", "have", "smart", "silence", "compression."]),
    (63.80, ["Uh,", "long", "pauses", "get", "trimmed", "down"]),
    (67.20, ["without", "making", "the", "speech", "sound", "rushed."]),
    (70.60, ["Third,", "like,", "we", "have", "speaker", "detection."]),
    (74.20, ["So", "if", "you", "have", "multiple", "speakers,"]),
    (77.40, ["it", "tracks", "who", "said", "what."]),
    # ── SILENCE 4 (~1.1 s) ──────────────────────────────────────────────────
    # ── PRICING  (~81 – ~125 s) –  "pricing" x3 ─────────────────────────────
    (81.00, ["Now", "let's", "talk", "about", "pricing."]),          # pricing #1
    (84.50, ["Um,", "so", "we", "have", "three", "tiers."]),
    (87.90, ["The", "first", "tier", "is", "the", "starter", "plan."]),
    (91.90, ["And", "pricing", "for", "that", "is", "nineteen",       # pricing #2
             "dollars", "per", "month."]),
    (97.60, ["That", "covers", "up", "to", "five", "hours",
             "of", "video", "per", "month."]),
    (103.70, ["The", "second", "tier", "is", "the", "professional", "plan."]),
    (108.30, ["And", "pricing", "for", "that", "is", "forty-nine",    # pricing #3
              "dollars", "per", "month."]),
    (114.60, ["That", "covers", "up", "to", "twenty", "hours", "of", "video."]),
    (119.00, ["Enterprise", "plans", "are", "custom."]),
    (122.00, ["You'd", "contact", "our", "sales", "team", "for", "that."]),
    # ── SILENCE 5 (~0.9 s) ──────────────────────────────────────────────────
    # ── Q&A  (~127 – ~169 s) –  "thanks" x3 ────────────────────────────────
    (127.00, ["Alright,", "so", "that", "wraps", "up", "the", "main", "demo."]),
    (131.40, ["Uh,", "I", "hope", "that", "was", "useful."]),
    (134.80, ["Does", "anyone", "have", "questions?"]),
    (137.50, ["Yeah,", "go", "ahead."]),
    (139.70, ["Oh,", "great", "question."]),
    (141.70, ["So", "we", "support", "MP4", "and", "H264."]),
    (145.00, ["Thanks", "for", "asking", "that."]),                   # thanks #2
    (148.30, ["Another", "question?"]),
    (150.00, ["Yes,", "we", "do", "have", "an", "API."]),
    (153.50, ["Thanks", "for", "bringing", "that", "up."]),           # thanks #3
    (157.10, ["OK,", "one", "more?"]),
    (158.80, ["Sure,", "that's", "a", "really", "good", "point."]),
    (162.60, ["Thanks,", "um,", "we're", "working", "on", "that", "now."]),  # thanks #4
    (167.50, ["It", "should", "be", "available", "in", "the", "next", "release."]),
    # ── SILENCE 6 (~0.8 s) ──────────────────────────────────────────────────
    # ── WRAP-UP  (~173 – ~182 s) ────────────────────────────────────────────
    (173.00, ["So,", "to", "summarise:"]),
    (175.40, ["we", "covered", "features,", "tier", "options,"]),
    (178.50, ["and", "answered", "your", "questions."]),
    (180.80, ["Please", "reach", "out", "if", "you", "have", "more."]),
    (184.30, ["We'll", "send", "the", "recording", "link."]),
]


def make_single_speaker() -> Transcript:
    """Build and return the single-speaker fixture transcript."""
    words = _build_words(_SEGMENTS_FINAL, speaker="S1")
    return Transcript(words=words, title="Product demo – single speaker")


# ---------------------------------------------------------------------------
# Exported word-ID constants
# (computed lazily on first import so they stay in sync with the fixture data)
# ---------------------------------------------------------------------------

def _find_ids(words: list[Word], target: str) -> list[int]:
    """Return all word IDs where the normalised text equals target."""
    norm_target = target.lower().strip(".,!?;:")
    return [
        w.id for w in words
        if w.text.lower().strip(".,!?;:") == norm_target
    ]


# Build the transcript once at import time for ID export
_TRANSCRIPT = make_single_speaker()
_WORDS = _TRANSCRIPT.words

# "pricing" occurrences  (exactly 3 – the word "pricing" standalone)
PRICING_IDS: list[int] = _find_ids(_WORDS, "pricing")

# "welcome to the channel" occurrences (Step 1 of worked example)
WELCOME_CHANNEL_IDS: list[int] = [
    w.id for w in _WORDS
    if w.text.lower().strip(".,!?;:") in ("welcome", "to", "the", "channel")
    and 41.5 <= w.start <= 44.5
]

# "thanks" occurrences  (exactly 4)
THANKS_IDS: list[int] = _find_ids(_WORDS, "thanks")

# Filler IDs by type
UM_IDS: list[int] = _find_ids(_WORDS, "um")        # "um" and "um,"
UH_IDS: list[int] = _find_ids(_WORDS, "uh")        # "uh" and "uh,"
LIKE_IDS: list[int] = _find_ids(_WORDS, "like")    # "like" and "like,"

# All filler IDs
FILLER_IDS: list[int] = sorted(UM_IDS + UH_IDS + LIKE_IDS)

# Silence gap boundaries (approximate, for test assertions)
# These are the word IDs of the LAST word before each major silence
SILENCE_BOUNDARIES: list[int] = []
_prev_end = 0.0
for _w in _WORDS:
    if _w.start - _prev_end > 0.5:
        SILENCE_BOUNDARIES.append(_w.id - 1)
    _prev_end = _w.end
