"""
Pure deterministic caption chunker for whole-video subtitling.

Partition rules (docs/RULES.md §CaptionChunking):
1. A word has `sentence_end == True`.
2. The gap between consecutive words is >= min_gap_secs (default 0.3 s).
3. Candidate space-joined text would exceed max_words (default 5) or max_chars (default 30).
NO repeated consecutive words heuristic.
"""

from __future__ import annotations

from promptcut.models.transcript import Transcript, Word


def chunk_transcript_for_captions(
    transcript: Transcript,
    max_words: int = 5,
    max_chars: int = 30,
    min_gap_secs: float = 0.3,
) -> list[list[Word]]:
    """
    Partition transcript words into subtitle chunks deterministically.

    Parameters
    ----------
    transcript : Transcript
        Source transcript with word timings and sentence_end flags.
    max_words : int
        Maximum number of words per caption line (default 5).
    max_chars : int
        Maximum character length of space-joined line text (default 30).
    min_gap_secs : float
        Silence gap between consecutive words that triggers a boundary (default 0.3s).

    Returns
    -------
    list[list[Word]]
        List of word lists, each representing a timed caption overlay.
    """
    if not transcript.words:
        return []

    chunks: list[list[Word]] = []
    current_chunk: list[Word] = []

    for word in transcript.words:
        if not current_chunk:
            current_chunk.append(word)
            if word.sentence_end:
                chunks.append(current_chunk)
                current_chunk = []
            continue

        prev_word = current_chunk[-1]
        gap = round(word.start - prev_word.end, 4)

        candidate_words = current_chunk + [word]
        candidate_text = " ".join(w.text for w in candidate_words)

        should_break = False

        # 1. Break at sentence_end on the preceding word
        if prev_word.sentence_end:
            should_break = True
        # 2. Break at silence gap >= 0.3s
        elif gap >= min_gap_secs:
            should_break = True
        # 3. Break if candidate exceeds max words (5) or max characters (30)
        elif len(candidate_words) > max_words or len(candidate_text) > max_chars:
            should_break = True

        if should_break:
            chunks.append(current_chunk)
            current_chunk = [word]
        else:
            current_chunk.append(word)

        if current_chunk and current_chunk[-1].sentence_end:
            chunks.append(current_chunk)
            current_chunk = []

    if current_chunk:
        chunks.append(current_chunk)

    return chunks
