"""
tests/test_transcription.py

Unit tests for transcription adapters and disk caching.
"""

from pathlib import Path
import pytest

from promptcut.models.transcript import Transcript, Word
from promptcut.transcription import (
    FasterWhisperAdapter,
    MockTranscriptionAdapter,
    load_cached_transcript,
)


@pytest.fixture
def dummy_media(tmp_path: Path) -> Path:
    f = tmp_path / "sample.mp4"
    f.write_bytes(b"dummy media content for sha256 hashing")
    return f


def test_mock_transcription_default(dummy_media: Path, tmp_path: Path):
    cache_dir = tmp_path / "cache"
    adapter = MockTranscriptionAdapter(use_cache=True, cache_dir=cache_dir)
    t = adapter.transcribe(dummy_media)

    assert isinstance(t, Transcript)
    assert len(t.words) > 0
    # Word IDs must be sequential 0, 1, 2, ...
    assert [w.id for w in t.words] == list(range(len(t.words)))

    # Verify cached on disk
    cached = load_cached_transcript(dummy_media, adapter.model_name, cache_dir=cache_dir)
    assert cached is not None
    assert [w.text for w in cached.words] == [w.text for w in t.words]


def test_mock_transcription_custom_fixture(dummy_media: Path, tmp_path: Path):
    custom_words = [
        Word(id=99, text="Custom", start=1.0, end=1.5),  # Out-of-order ID to test normalization
        Word(id=105, text="Transcript", start=1.6, end=2.0),
    ]
    raw_fixture = Transcript(words=[
        Word(id=0, text="Custom", start=1.0, end=1.5),
        Word(id=1, text="Transcript", start=1.6, end=2.0),
    ])

    adapter = MockTranscriptionAdapter(fixture_transcript=raw_fixture, use_cache=False)
    t = adapter.transcribe(dummy_media)

    assert len(t.words) == 2
    assert t.words[0].id == 0
    assert t.words[0].text == "Custom"
    assert t.words[1].id == 1
    assert t.words[1].text == "Transcript"


def test_faster_whisper_adapter_properties():
    adapter = FasterWhisperAdapter(model_size_or_path="tiny", device="cpu")
    assert adapter.model_name == "faster-whisper-tiny"


def test_faster_whisper_missing_dep(dummy_media: Path):
    adapter = FasterWhisperAdapter()
    # If faster-whisper is not installed, transcribe must raise ImportError with instructions
    try:
        import faster_whisper
        # If installed, skip this test
    except ImportError:
        with pytest.raises(ImportError, match="faster-whisper is not installed"):
            adapter.transcribe(dummy_media, source_hash="abc123hash")


def test_transcription_with_explicit_source_hash(dummy_media: Path, tmp_path: Path):
    cache_dir = tmp_path / "cache"
    adapter = MockTranscriptionAdapter(use_cache=True, cache_dir=cache_dir)
    explicit_hash = "f4c28e93b1a"
    t = adapter.transcribe(dummy_media, source_hash=explicit_hash)

    assert isinstance(t, Transcript)
    # Cached file should be named using explicit_hash
    expected_cache_file = cache_dir / f"{explicit_hash}_{adapter.model_name}.json"
    assert expected_cache_file.exists()

    cached = load_cached_transcript(
        dummy_media, adapter.model_name, cache_dir=cache_dir, source_hash=explicit_hash
    )
    assert cached is not None
    assert len(cached.words) == len(t.words)


def test_vosk_adapter_properties():
    from promptcut.transcription import VoskTranscriptionAdapter

    adapter = VoskTranscriptionAdapter(lang="en-us")
    assert adapter.model_name == "vosk-en-us"


def test_groq_whisper_adapter_properties():
    from promptcut.transcription import GroqWhisperAdapter

    adapter = GroqWhisperAdapter(api_key="mock_key", model_name="whisper-large-v3-turbo")
    assert adapter.model_name == "groq-whisper-large-v3-turbo"


