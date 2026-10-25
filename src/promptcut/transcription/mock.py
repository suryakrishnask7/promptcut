"""
Mock Transcription Adapter.

Loads pre-computed Transcript fixtures or generates synthetic transcripts
for offline test suites with zero external model or API key requirements.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptcut.models.transcript import Transcript, Word
from promptcut.transcription.base import TranscriptionAdapter
from promptcut.transcription.cache import load_cached_transcript, save_cached_transcript


class MockTranscriptionAdapter(TranscriptionAdapter):
    """
    Mock transcription provider that returns pre-configured or synthetic transcripts.
    """

    def __init__(
        self,
        fixture_transcript: Transcript | str | Path | None = None,
        fixture_path: str | Path | None = None,
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ):
        if isinstance(fixture_transcript, (str, Path)):
            self._fixture_transcript = None
            self._fixture_path = Path(fixture_transcript)
        else:
            self._fixture_transcript = fixture_transcript
            self._fixture_path = Path(fixture_path) if fixture_path else None
        self._use_cache = use_cache
        self._cache_dir = cache_dir

    @property
    def model_name(self) -> str:
        return "mock-stt"

    def transcribe(
        self,
        media_path: str | Path,
        language: str = "en",
        source_hash: str | None = None,
    ) -> Transcript:
        path = Path(media_path)

        # If an explicit fixture transcript is provided, prioritize it over generic cache
        if self._fixture_transcript is not None:
            return self._normalize_transcript(self._fixture_transcript)
        if self._fixture_path and self._fixture_path.exists():
            data = json.loads(self._fixture_path.read_text(encoding="utf-8"))
            raw = Transcript.model_validate(data)
            return self._normalize_transcript(raw)

        # Check disk cache first before falling back to synthetic generator
        if self._use_cache and path.exists():
            if cached := load_cached_transcript(
                path, self.model_name, self._cache_dir, source_hash=source_hash
            ):
                return cached

        # Fallback default synthetic transcript
        result = self._generate_default_transcript(path)

        if self._use_cache and path.exists():
            save_cached_transcript(
                path, self.model_name, result, self._cache_dir, source_hash=source_hash
            )

        return result


    def _normalize_transcript(self, raw: Transcript) -> Transcript:
        """Ensure word IDs are strictly sequential integers starting from 0."""
        new_words: list[Word] = []
        for i, w in enumerate(raw.words):
            new_words.append(
                Word(
                    id=i,
                    text=w.text,
                    start=w.start,
                    end=w.end,
                    speaker=w.speaker or "S1",
                    confidence=w.confidence,
                    sentence_end=getattr(w, "sentence_end", False) or bool(w.text and w.text.strip()[-1] in ".!?"),
                )
            )
        return Transcript(
            words=new_words,
            title=raw.title or "Transcribed Video",
            source_duration=raw.source_duration,
        )

    def _generate_default_transcript(self, path: Path | str) -> Transcript:
        """Generate a realistic synthetic transcript scaled to media duration."""
        p = Path(path)
        title = p.name or "Transcribed Video"

        duration = 0.0
        try:
            from promptcut.media.intake import inspect_media

            if p.exists() and p.is_file():
                meta = inspect_media(p)
                duration = meta.duration
        except Exception:
            pass

        sample_sentences = [
            ["Welcome", "to", "this", "video", "demonstration."],
            ["Today", "we", "are", "testing", "prompt", "cut", "video", "editing."],
            ["You", "can", "cut", "silence", "and", "remove", "filler", "words."],
            ["Captions", "can", "be", "styled", "and", "previewed", "in", "realtime."],
            ["Thanks", "for", "watching", "and", "have", "a", "great", "day."],
        ]

        words: list[Word] = []
        wid = 0
        t = 0.5
        target_duration = duration if duration > 1.0 else 3.5

        sentence_idx = 0
        while t < target_duration - 0.4 and wid < 200:
            sentence = sample_sentences[sentence_idx % len(sample_sentences)]
            speaker = f"S{(sentence_idx % 2) + 1}"
            sentence_idx += 1
            for txt in sentence:
                if t >= target_duration - 0.2:
                    break
                dur = min(0.35, max(0.2, (target_duration - t) / 2))
                words.append(
                    Word(
                        id=wid,
                        text=txt,
                        start=round(t, 3),
                        end=round(t + dur, 3),
                        speaker=speaker,
                        confidence=0.98,
                    )
                )
                wid += 1
                t += dur + 0.08
            t += 0.4  # Pause between sentences for natural silence

        if not words:
            # Fallback minimum
            texts = ["Welcome", "to", "the", "channel.", "Thanks", "for", "watching."]
            t = 0.5
            for i, txt in enumerate(texts):
                words.append(
                    Word(
                        id=i,
                        text=txt,
                        start=round(t, 3),
                        end=round(t + 0.35, 3),
                        speaker="S1",
                        confidence=0.98,
                    )
                )
                t += 0.45

        return Transcript(
            words=words,
            title=title,
            source_duration=round(duration, 3) if duration > 0 else round(words[-1].end + 0.5, 3),
        )

